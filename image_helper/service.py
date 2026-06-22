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

        neighbors = client.search_neighbors(
            parse_local_datetime(asset["localDateTime"]),
            window_seconds=max(settings.wiggle_time_window_seconds * 4, 30),
        )

        batch: list[AssetRecord] = []
        indexed_any = False
        for neighbor in neighbors:
            record = prepare_index_record(client, store, neighbor)
            if record is None:
                continue
            batch.append(record)
            if len(batch) >= INDEX_BATCH_SIZE:
                if flush_index_batch(store, batch) > 0:
                    indexed_any = True
        if flush_index_batch(store, batch) > 0:
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
