from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from image_helper.asset_metadata import extract_asset_metadata
from image_helper.config import Settings
from image_helper.detector import find_wiggle_groups
from image_helper.exporter import export_wiggle_group
from image_helper.group_validation import RejectedWiggleGroup, partition_wiggle_groups
from image_helper.hashstore import HashStore, dimensions_from_image_bytes
from image_helper.immich import ImmichClient, ImmichError, parse_local_datetime
from image_helper.models import AssetRecord, WiggleGroup


@dataclass
class ExportSummary:
    exported: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass
class DetectResult:
    accepted: list[WiggleGroup]
    rejected: list[RejectedWiggleGroup]


INDEX_BATCH_SIZE = 50


def _image_bytes_for_index(
    client: ImmichClient,
    asset_id: str,
    *,
    hash_source: str,
) -> bytes:
    if hash_source == "thumbnail":
        return client.download_thumbnail(asset_id)
    return client.download_original(asset_id)


def prepare_index_record(
    client: ImmichClient,
    store: HashStore,
    asset: dict,
    *,
    force: bool = False,
    hash_source: str = "original",
) -> AssetRecord | None:
    asset_id = asset["id"]
    checksum = asset.get("checksum")
    existing = store.get(asset_id)

    if existing and not force:
        if checksum and existing.checksum == checksum:
            return None

    metadata = extract_asset_metadata(asset)
    width = metadata["width"]
    height = metadata["height"]

    try:
        image_bytes = _image_bytes_for_index(client, asset_id, hash_source=hash_source)
    except ImmichError:
        if hash_source == "original":
            image_bytes = client.download_thumbnail(asset_id)
        else:
            raise

    phash = client.compute_phash_from_bytes(image_bytes)
    if width is None or height is None:
        try:
            width, height = dimensions_from_image_bytes(image_bytes)
        except Exception:
            width = width
            height = height

    return AssetRecord(
        asset_id=asset_id,
        phash=phash,
        local_datetime=parse_local_datetime(asset["localDateTime"]),
        checksum=checksum,
        width=width,
        height=height,
        original_file_name=metadata["original_file_name"],
        stack_id=metadata["stack_id"],
        is_primary_in_stack=metadata["is_primary_in_stack"],
    )


def index_asset(
    client: ImmichClient,
    store: HashStore,
    asset: dict,
    *,
    force: bool = False,
    hash_source: str = "original",
) -> bool:
    record = prepare_index_record(
        client,
        store,
        asset,
        force=force,
        hash_source=hash_source,
    )
    if record is None:
        return False
    store.upsert(record)
    return True


def flush_index_batch(store: HashStore, batch: list[AssetRecord]) -> int:
    if not batch:
        return 0
    store.upsert_many(batch)
    count = len(batch)
    batch.clear()
    return count


def _find_raw_groups(
    records: list[AssetRecord],
    settings: Settings,
) -> list[WiggleGroup]:
    return find_wiggle_groups(
        records,
        threshold=settings.wiggle_threshold,
        time_window_seconds=settings.wiggle_time_window_seconds,
    )


def detect_groups_with_validation(
    settings: Settings,
    records: list[AssetRecord],
) -> DetectResult:
    raw_groups = _find_raw_groups(records, settings)
    accepted, rejected = partition_wiggle_groups(raw_groups, settings)
    return DetectResult(accepted=accepted, rejected=rejected)


def detect_groups(settings: Settings, store: HashStore) -> list[WiggleGroup]:
    return detect_groups_with_validation(settings, store.list_all()).accepted


def detect_groups_detailed(settings: Settings, store: HashStore) -> DetectResult:
    return detect_groups_with_validation(settings, store.list_all())


def detect_groups_in_range(
    settings: Settings,
    store: HashStore,
    *,
    center: datetime,
    window_seconds: float,
) -> DetectResult:
    margin = max(window_seconds, settings.wiggle_time_window_seconds * 4)
    start = center - timedelta(seconds=margin)
    end = center + timedelta(seconds=margin)
    records = store.list_in_range(start, end)
    return detect_groups_with_validation(settings, records)


def export_groups(
    settings: Settings,
    store: HashStore,
    groups: list[WiggleGroup],
) -> ExportSummary:
    summary = ExportSummary()
    if not groups:
        return summary

    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        album = client.get_or_create_album(settings.wiggle_album_name)
        album_id = album["id"]

        for group in groups:
            if store.is_exported(group.group_key):
                summary.skipped += 1
                continue

            try:
                uploaded = export_wiggle_group(
                    client,
                    group,
                    frame_duration_ms=settings.wiggle_frame_duration_ms,
                    max_size=settings.wiggle_max_size,
                    boomerang=settings.wiggle_boomerang,
                    device_id=settings.device_id,
                    frame_fit=settings.wiggle_frame_fit,
                )
                gif_asset_id = uploaded.get("id")
                if not gif_asset_id:
                    raise ImmichError(f"Upload response missing asset id: {uploaded}")

                client.add_assets_to_album(album_id, [gif_asset_id])
                store.mark_exported(group.group_key, gif_asset_id)
                summary.exported += 1
            except ImmichError:
                summary.errors += 1

    return summary


def resolve_webhook_asset(client: ImmichClient, asset: dict) -> tuple[dict, bool]:
    if asset.get("localDateTime"):
        return asset, False

    asset_id = asset["id"]
    full_asset = client.get_asset(asset_id)
    return full_asset, True


def process_webhook_asset_id(
    settings: Settings,
    store: HashStore,
    asset_id: str,
    *,
    raw_asset: dict | None = None,
    trigger: str | None = None,
) -> dict[str, int | bool | str | None]:
    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        asset = raw_asset or {"id": asset_id}
        asset, resolved = resolve_webhook_asset(client, asset)
        client.wait_for_thumbnail(asset_id)

        neighbor_window = max(settings.wiggle_time_window_seconds * 4, 30)
        with_stacked = (
            False if settings.wiggle_neighbor_search_primary_only else None
        )
        neighbors = client.search_neighbors(
            parse_local_datetime(asset["localDateTime"]),
            window_seconds=neighbor_window,
            with_stacked=with_stacked,
        )

        batch: list[AssetRecord] = []
        indexed_any = False
        for neighbor in neighbors:
            record = prepare_index_record(
                client,
                store,
                neighbor,
                hash_source=settings.wiggle_hash_source,
            )
            if record is None:
                continue
            batch.append(record)
            if len(batch) >= INDEX_BATCH_SIZE:
                if flush_index_batch(store, batch) > 0:
                    indexed_any = True
        if flush_index_batch(store, batch) > 0:
            indexed_any = True

        detection = detect_groups_in_range(
            settings,
            store,
            center=parse_local_datetime(asset["localDateTime"]),
            window_seconds=neighbor_window,
        )
        relevant = [
            group
            for group in detection.accepted
            if any(member.asset_id == asset_id for member in group.assets)
        ]
        pending = [group for group in relevant if not store.is_exported(group.group_key)]

        exported = 0
        if pending:
            summary = export_groups(settings, store, pending)
            exported = summary.exported

    return {
        "trigger": trigger,
        "resolved_asset": resolved,
        "indexed_neighbors": indexed_any,
        "groups_found": len(relevant),
        "exported": exported,
    }


def process_webhook_asset(
    settings: Settings,
    store: HashStore,
    asset: dict,
) -> dict[str, int | bool | str | None]:
    return process_webhook_asset_id(
        settings,
        store,
        asset["id"],
        raw_asset=asset,
    )
