from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from image_helper.asset_metadata import extract_asset_metadata
from image_helper.config import Settings
from image_helper.detector import find_wiggle_groups
from image_helper.exporter import StabilizeOptions, export_wiggle_group, make_wigglegram_bytes
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


@dataclass
class IndexSummary:
    added: int = 0
    skipped: int = 0
    errors: int = 0
    indexed_records: list[AssetRecord] | None = None


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
        burst_id=metadata["burst_id"],
        burst_sequence=metadata["burst_sequence"],
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
        max_gap_frames=settings.wiggle_max_gap_frames,
    )


def detect_groups_with_validation(
    settings: Settings,
    records: list[AssetRecord],
) -> DetectResult:
    raw_groups = _find_raw_groups(records, settings)
    accepted, rejected = partition_wiggle_groups(raw_groups, settings)
    return DetectResult(accepted=accepted, rejected=rejected)


def merge_detect_results(results: list[DetectResult]) -> DetectResult:
    accepted_by_key: dict[str, WiggleGroup] = {}
    rejected_by_key: dict[str, RejectedWiggleGroup] = {}

    for result in results:
        for group in result.accepted:
            accepted_by_key[group.group_key] = group
            rejected_by_key.pop(group.group_key, None)
        for entry in result.rejected:
            if entry.group.group_key not in accepted_by_key:
                rejected_by_key[entry.group.group_key] = entry

    accepted = sorted(
        accepted_by_key.values(),
        key=lambda group: group.assets[0].local_datetime,
    )
    rejected = sorted(
        rejected_by_key.values(),
        key=lambda entry: entry.group.assets[0].local_datetime,
    )
    return DetectResult(accepted=accepted, rejected=rejected)


def detection_window_seconds(settings: Settings, *, window_seconds: float | None = None) -> float:
    base = window_seconds if window_seconds is not None else settings.wiggle_time_window_seconds
    return max(base, settings.wiggle_time_window_seconds * 4, 30)


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
    margin = detection_window_seconds(settings, window_seconds=window_seconds)
    start = center - timedelta(seconds=margin)
    end = center + timedelta(seconds=margin)
    records = store.list_in_range(start, end)
    return detect_groups_with_validation(settings, records)


def detect_groups_for_centers(
    settings: Settings,
    store: HashStore,
    centers: list[datetime],
    *,
    window_seconds: float | None = None,
) -> DetectResult:
    if not centers:
        return DetectResult(accepted=[], rejected=[])

    margin = detection_window_seconds(settings, window_seconds=window_seconds)
    results: list[DetectResult] = []
    for center in centers:
        results.append(
            detect_groups_in_range(
                settings,
                store,
                center=center,
                window_seconds=margin,
            )
        )
    return merge_detect_results(results)


def filter_ready_groups(
    store: HashStore,
    groups: list[WiggleGroup],
    *,
    settle_seconds: float,
    now: datetime | None = None,
) -> list[WiggleGroup]:
    if settle_seconds <= 0:
        return groups

    ready_keys = set(
        store.list_ready_pending_group_keys(
            settle_seconds=settle_seconds,
            now=now,
        )
    )
    return [group for group in groups if group.group_key in ready_keys]


def apply_settle_filter(
    settings: Settings,
    store: HashStore,
    groups: list[WiggleGroup],
    *,
    now: datetime | None = None,
) -> list[WiggleGroup]:
    if settings.wiggle_settle_seconds <= 0:
        return groups

    current = now or datetime.now(timezone.utc)
    active_keys: set[str] = set()

    for group in groups:
        if store.is_exported(group.group_key):
            continue
        active_keys.add(group.group_key)
        store.touch_pending_group(group.group_key, seen_at=current)

    store.prune_pending_groups(active_keys)
    return filter_ready_groups(
        store,
        groups,
        settle_seconds=settings.wiggle_settle_seconds,
        now=current,
    )


def collect_export_candidates(
    settings: Settings,
    store: HashStore,
    groups: list[WiggleGroup],
    *,
    now: datetime | None = None,
) -> list[WiggleGroup]:
    pending = [group for group in groups if not store.is_exported(group.group_key)]
    return apply_settle_filter(settings, store, pending, now=now)


def index_assets(
    client: ImmichClient,
    store: HashStore,
    assets,
    *,
    force: bool = False,
    hash_source: str = "original",
    workers: int = 1,
    base_url: str | None = None,
    api_key: str | None = None,
) -> IndexSummary:
    asset_list = list(assets)
    if not asset_list:
        return IndexSummary(indexed_records=[])

    if workers <= 1:
        return _index_assets_sequential(
            client,
            store,
            asset_list,
            force=force,
            hash_source=hash_source,
        )

    if base_url is None or api_key is None:
        base_url = client.base_url
        api_key = client._client.headers.get("x-api-key", "")

    return _index_assets_parallel(
        store,
        asset_list,
        base_url=base_url,
        api_key=api_key,
        force=force,
        hash_source=hash_source,
        workers=workers,
    )


def _index_assets_sequential(
    client: ImmichClient,
    store: HashStore,
    assets: list[dict],
    *,
    force: bool,
    hash_source: str,
) -> IndexSummary:
    summary = IndexSummary(indexed_records=[])
    batch: list[AssetRecord] = []

    for asset in assets:
        try:
            record = prepare_index_record(
                client,
                store,
                asset,
                force=force,
                hash_source=hash_source,
            )
            if record is None:
                summary.skipped += 1
                continue

            batch.append(record)
            if len(batch) >= INDEX_BATCH_SIZE:
                summary.added += flush_index_batch(store, batch)
        except ImmichError:
            summary.errors += 1

    summary.added += flush_index_batch(store, batch)
    if summary.added:
        summary.indexed_records = _records_for_assets(store, assets)
    return summary


def _index_assets_parallel(
    store: HashStore,
    assets: list[dict],
    *,
    base_url: str,
    api_key: str,
    force: bool,
    hash_source: str,
    workers: int,
) -> IndexSummary:
    summary = IndexSummary(indexed_records=[])
    batch: list[AssetRecord] = []

    def _prepare(asset: dict) -> AssetRecord | None:
        with ImmichClient(base_url, api_key) as worker_client:
            return prepare_index_record(
                worker_client,
                store,
                asset,
                force=force,
                hash_source=hash_source,
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_prepare, asset): asset for asset in assets}
        for future in as_completed(futures):
            try:
                record = future.result()
            except ImmichError:
                summary.errors += 1
                continue

            if record is None:
                summary.skipped += 1
                continue

            batch.append(record)
            if len(batch) >= INDEX_BATCH_SIZE:
                summary.added += flush_index_batch(store, batch)

    summary.added += flush_index_batch(store, batch)
    if summary.added:
        summary.indexed_records = _records_for_assets(store, assets)
    return summary


def _records_for_assets(store: HashStore, assets: list[dict]) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    for asset in assets:
        record = store.get(asset["id"])
        if record is not None:
            records.append(record)
    return records


def run_daemon_detection(
    settings: Settings,
    store: HashStore,
    indexed_records: list[AssetRecord],
) -> DetectResult:
    if not indexed_records:
        ready_keys = store.list_ready_pending_group_keys(
            settle_seconds=settings.wiggle_settle_seconds,
        )
        if not ready_keys:
            return DetectResult(accepted=[], rejected=[])

        all_groups = detect_groups_with_validation(settings, store.list_all()).accepted
        accepted = [group for group in all_groups if group.group_key in ready_keys]
        return DetectResult(accepted=accepted, rejected=[])

    centers = [record.local_datetime for record in indexed_records]
    return detect_groups_for_centers(settings, store, centers)


def stabilize_options_from_settings(settings: Settings) -> StabilizeOptions:
    mode = settings.wiggle_stabilize_mode
    if not settings.wiggle_stabilize:
        mode = "off"
    return StabilizeOptions(
        enabled=settings.wiggle_stabilize,
        mode=mode,
        reference=settings.wiggle_stabilize_reference,
        crop_to_overlap=settings.wiggle_stabilize_crop,
        max_rotation_deg=settings.wiggle_stabilize_max_rotation_deg,
        working_max_edge=settings.wiggle_stabilize_working_max_edge,
    )


def preview_wiggle_group(
    settings: Settings,
    store: HashStore,
    group: WiggleGroup,
    *,
    output_path,
) -> None:
    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        gif_bytes = make_wigglegram_bytes(
            client,
            group,
            frame_duration_ms=settings.wiggle_frame_duration_ms,
            max_size=settings.wiggle_max_size,
            boomerang=settings.wiggle_boomerang,
            frame_fit=settings.wiggle_frame_fit,
            stabilize=stabilize_options_from_settings(settings),
        )
    output_path.write_bytes(gif_bytes)


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
                    stabilize=stabilize_options_from_settings(settings),
                )
                gif_asset_id = uploaded.get("id")
                if not gif_asset_id:
                    raise ImmichError(f"Upload response missing asset id: {uploaded}")

                client.add_assets_to_album(album_id, [gif_asset_id])

                if settings.wiggle_stack_with_sources:
                    source_ids = [asset.asset_id for asset in group.assets]
                    client.create_stack([gif_asset_id, *source_ids])

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

        neighbor_window = detection_window_seconds(settings)
        with_stacked = (
            False if settings.wiggle_neighbor_search_primary_only else None
        )
        neighbors = client.search_neighbors(
            parse_local_datetime(asset["localDateTime"]),
            window_seconds=neighbor_window,
            with_stacked=with_stacked,
        )

        index_summary = index_assets(
            client,
            store,
            neighbors,
            hash_source=settings.wiggle_hash_source,
            workers=settings.index_workers,
            base_url=settings.immich_base_url,
            api_key=settings.immich_api_key,
        )

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
        pending = collect_export_candidates(settings, store, relevant)

        exported = 0
        if pending:
            summary = export_groups(settings, store, pending)
            exported = summary.exported

    return {
        "trigger": trigger,
        "resolved_asset": resolved,
        "indexed_neighbors": bool(index_summary.added),
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
