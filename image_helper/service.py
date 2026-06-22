from __future__ import annotations

from dataclasses import dataclass

from image_helper.config import Settings
from image_helper.detector import find_wiggle_groups
from image_helper.exporter import export_wiggle_group
from image_helper.hashstore import HashStore
from image_helper.immich import ImmichClient, ImmichError, parse_local_datetime
from image_helper.models import AssetRecord, WiggleGroup


@dataclass
class ExportSummary:
    exported: int = 0
    skipped: int = 0
    errors: int = 0


INDEX_BATCH_SIZE = 50


def prepare_index_record(
    client: ImmichClient,
    store: HashStore,
    asset: dict,
    *,
    force: bool = False,
) -> AssetRecord | None:
    asset_id = asset["id"]
    checksum = asset.get("checksum")
    existing = store.get(asset_id)

    if existing and not force:
        if checksum and existing.checksum == checksum:
            return None

    phash = client.hash_asset_thumbnail(asset_id)
    return AssetRecord(
        asset_id=asset_id,
        phash=phash,
        local_datetime=parse_local_datetime(asset["localDateTime"]),
        checksum=checksum,
    )


def index_asset(
    client: ImmichClient,
    store: HashStore,
    asset: dict,
    *,
    force: bool = False,
) -> bool:
    record = prepare_index_record(client, store, asset, force=force)
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


def detect_groups(settings: Settings, store: HashStore) -> list[WiggleGroup]:
    records = store.list_all()
    return find_wiggle_groups(
        records,
        threshold=settings.wiggle_threshold,
        time_window_seconds=settings.wiggle_time_window_seconds,
    )


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


def process_webhook_asset(
    settings: Settings,
    store: HashStore,
    asset: dict,
) -> dict[str, int | bool]:
    asset_id = asset["id"]

    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        neighbors = client.search_neighbors(
            parse_local_datetime(asset["localDateTime"]),
            window_seconds=max(settings.wiggle_time_window_seconds * 4, 30),
        )

        indexed_any = False
        for neighbor in neighbors:
            if index_asset(client, store, neighbor):
                indexed_any = True

        groups = detect_groups(settings, store)
        relevant = [
            group
            for group in groups
            if any(member.asset_id == asset_id for member in group.assets)
        ]
        pending = [group for group in relevant if not store.is_exported(group.group_key)]

        exported = 0
        if pending:
            summary = export_groups(settings, store, pending)
            exported = summary.exported

    return {
        "indexed_neighbors": indexed_any,
        "groups_found": len(relevant),
        "exported": exported,
    }
