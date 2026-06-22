from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from image_helper.asset_metadata import extract_asset_metadata
from image_helper.byte_cache import ImageByteCache, default_byte_cache_dir
from image_helper.config import Settings
from image_helper.detector import find_wiggle_groups
from image_helper.exporter import (
    ExportOptions,
    StabilizeOptions,
    export_wiggle_group,
    make_wigglegram_bytes,
)
from image_helper.group_validation import RejectedWiggleGroup, partition_wiggle_groups
from image_helper.hashstore import HashStore, dimensions_from_image_bytes
from image_helper.immich import ImmichClient, ImmichError, parse_local_datetime
from image_helper.frames import OutputFormat
from image_helper.models import AssetRecord, WiggleGroup

_DETECT_CHUNK_SECONDS = 86400.0


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
    checksum: str | None = None,
    byte_cache: "ImageByteCache | None" = None,
) -> bytes:
    if byte_cache is not None and hash_source == "original":
        cached = byte_cache.get(asset_id, checksum)
        if cached is not None:
            return cached
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
    existing: AssetRecord | None = None,
    byte_cache: ImageByteCache | None = None,
) -> AssetRecord | None:
    asset_id = asset["id"]
    checksum = asset.get("checksum")
    if existing is None:
        existing = store.get(asset_id)

    if existing and not force:
        if checksum and existing.checksum == checksum:
            return None

    metadata = extract_asset_metadata(asset)
    width = metadata["width"]
    height = metadata["height"]

    try:
        image_bytes = _image_bytes_for_index(
            client,
            asset_id,
            hash_source=hash_source,
            checksum=checksum,
            byte_cache=byte_cache,
        )
    except ImmichError:
        if hash_source == "original":
            image_bytes = client.download_thumbnail(asset_id)
        else:
            raise

    if byte_cache is not None and hash_source == "original":
        byte_cache.store(asset_id, checksum, image_bytes)

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


def byte_cache_from_settings(settings: Settings) -> ImageByteCache:
    cache_dir = settings.wiggle_cache_dir
    if cache_dir is None:
        cache_dir = default_byte_cache_dir()
    return ImageByteCache(
        max_entries=settings.wiggle_cache_memory_entries,
        cache_dir=cache_dir,
    )


def output_formats_for_settings(settings: Settings) -> list[OutputFormat]:
    if settings.wiggle_output_format == "both":
        return ["webp", "gif"]
    if settings.wiggle_output_format == "webp" and settings.wiggle_gif_fallback:
        return ["webp", "gif"]
    return [settings.wiggle_output_format]


def _detect_records_chunked(
    settings: Settings,
    records: list[AssetRecord],
    *,
    chunk_seconds: float = _DETECT_CHUNK_SECONDS,
) -> DetectResult:
    if not records:
        return DetectResult(accepted=[], rejected=[])

    sorted_records = sorted(records, key=lambda asset: asset.local_datetime)
    margin = detection_window_seconds(settings)
    chunk_delta = timedelta(seconds=chunk_seconds)
    results: list[DetectResult] = []
    index = 0

    while index < len(sorted_records):
        chunk_start_time = sorted_records[index].local_datetime
        chunk_end_time = chunk_start_time + chunk_delta
        range_start = chunk_start_time - timedelta(seconds=margin)
        range_end = chunk_end_time + timedelta(seconds=margin)
        chunk_records = [
            record
            for record in sorted_records
            if range_start <= record.local_datetime <= range_end
        ]
        results.append(detect_groups_with_validation(settings, chunk_records))

        next_index = index + 1
        while next_index < len(sorted_records) and sorted_records[next_index].local_datetime <= chunk_end_time:
            next_index += 1
        if next_index == index:
            next_index += 1
        index = next_index

    return merge_detect_results(results)


def detect_groups(settings: Settings, store: HashStore) -> list[WiggleGroup]:
    return detect_groups_detailed(settings, store).accepted


def detect_groups_detailed(settings: Settings, store: HashStore) -> DetectResult:
    records = store.list_all()
    if len(records) <= 5000:
        return detect_groups_with_validation(settings, records)
    return _detect_records_chunked(settings, records)


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
    margin_delta = timedelta(seconds=margin)
    sorted_centers = sorted(centers)
    ranges: list[tuple[datetime, datetime]] = []
    range_start = sorted_centers[0] - margin_delta
    range_end = sorted_centers[0] + margin_delta

    for center in sorted_centers[1:]:
        start = center - margin_delta
        end = center + margin_delta
        if start <= range_end:
            range_end = max(range_end, end)
            continue
        ranges.append((range_start, range_end))
        range_start, range_end = start, end
    ranges.append((range_start, range_end))

    results: list[DetectResult] = []
    for start, end in ranges:
        records = store.list_in_range(start, end)
        results.append(detect_groups_with_validation(settings, records))
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
    exported_keys = store.exported_group_keys([group.group_key for group in groups])
    active_keys = [
        group.group_key
        for group in groups
        if group.group_key not in exported_keys
    ]
    store.touch_pending_groups(active_keys, seen_at=current)
    store.prune_pending_groups(set(active_keys))
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
    exported_keys = store.exported_group_keys([group.group_key for group in groups])
    pending = [group for group in groups if group.group_key not in exported_keys]
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
    byte_cache: ImageByteCache | None = None,
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
            byte_cache=byte_cache,
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
        byte_cache=byte_cache,
    )


def _index_assets_sequential(
    client: ImmichClient,
    store: HashStore,
    assets: list[dict],
    *,
    force: bool,
    hash_source: str,
    byte_cache: ImageByteCache | None = None,
) -> IndexSummary:
    summary = IndexSummary(indexed_records=[])
    batch: list[AssetRecord] = []
    existing_by_id = store.get_many([asset["id"] for asset in assets])

    for asset in assets:
        try:
            record = prepare_index_record(
                client,
                store,
                asset,
                force=force,
                hash_source=hash_source,
                existing=existing_by_id.get(asset["id"]),
                byte_cache=byte_cache,
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
    byte_cache: ImageByteCache | None = None,
) -> IndexSummary:
    summary = IndexSummary(indexed_records=[])
    batch: list[AssetRecord] = []
    existing_by_id = store.get_many([asset["id"] for asset in assets])
    thread_local = threading.local()
    worker_clients: list[ImmichClient] = []
    clients_lock = threading.Lock()

    def _init_worker() -> None:
        client = ImmichClient(base_url, api_key)
        thread_local.client = client
        with clients_lock:
            worker_clients.append(client)

    def _prepare(asset: dict) -> AssetRecord | None:
        return prepare_index_record(
            thread_local.client,
            store,
            asset,
            force=force,
            hash_source=hash_source,
            existing=existing_by_id.get(asset["id"]),
            byte_cache=byte_cache,
        )

    with ThreadPoolExecutor(max_workers=workers, initializer=_init_worker) as executor:
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

    for client in worker_clients:
        client.close()

    summary.added += flush_index_batch(store, batch)
    if summary.added:
        summary.indexed_records = _records_for_assets(store, assets)
    return summary


def _records_for_assets(store: HashStore, assets: list[dict]) -> list[AssetRecord]:
    by_id = store.get_many([asset["id"] for asset in assets])
    return [by_id[asset_id] for asset_id in by_id]


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

        all_groups = detect_groups_detailed(settings, store).accepted
        accepted = [group for group in all_groups if group.group_key in ready_keys]
        return DetectResult(accepted=accepted, rejected=[])

    centers = [record.local_datetime for record in indexed_records]
    return detect_groups_for_centers(settings, store, centers)


def export_options_from_settings(
    settings: Settings,
    *,
    output_format: OutputFormat,
) -> ExportOptions:
    return ExportOptions(
        frame_duration_ms=settings.wiggle_frame_duration_ms,
        max_size=settings.wiggle_max_size,
        boomerang=settings.wiggle_boomerang,
        frame_fit=settings.wiggle_frame_fit,
        stabilize=stabilize_options_from_settings(settings),
        output_format=output_format,
        webp_quality=settings.wiggle_webp_quality,
        webp_lossless=settings.wiggle_webp_lossless,
        gif_dither=settings.wiggle_gif_dither,
        download_workers=settings.wiggle_download_workers,
    )


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
    output_format = output_formats_for_settings(settings)[0]
    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        artifact_bytes = make_wigglegram_bytes(
            client,
            group,
            frame_duration_ms=settings.wiggle_frame_duration_ms,
            max_size=settings.wiggle_max_size,
            boomerang=settings.wiggle_boomerang,
            frame_fit=settings.wiggle_frame_fit,
            stabilize=stabilize_options_from_settings(settings),
            output_format=output_format,
            webp_quality=settings.wiggle_webp_quality,
            webp_lossless=settings.wiggle_webp_lossless,
            gif_dither=settings.wiggle_gif_dither,
            download_workers=settings.wiggle_download_workers,
            byte_cache=byte_cache_from_settings(settings),
        )
    output_path.write_bytes(artifact_bytes)


def _export_single_group(
    client: ImmichClient,
    store: HashStore,
    settings: Settings,
    group: WiggleGroup,
    *,
    album_id: str,
    byte_cache: ImageByteCache,
) -> bool:
    formats = output_formats_for_settings(settings)
    uploaded_ids: list[str] = []

    for output_format in formats:
        uploaded = export_wiggle_group(
            client,
            group,
            frame_duration_ms=settings.wiggle_frame_duration_ms,
            max_size=settings.wiggle_max_size,
            boomerang=settings.wiggle_boomerang,
            device_id=settings.device_id,
            frame_fit=settings.wiggle_frame_fit,
            stabilize=stabilize_options_from_settings(settings),
            output_format=output_format,
            webp_quality=settings.wiggle_webp_quality,
            webp_lossless=settings.wiggle_webp_lossless,
            gif_dither=settings.wiggle_gif_dither,
            download_workers=settings.wiggle_download_workers,
            byte_cache=byte_cache,
            device_asset_id_suffix=output_format,
        )
        asset_id = uploaded.get("id")
        if not asset_id:
            raise ImmichError(f"Upload response missing asset id: {uploaded}")
        uploaded_ids.append(asset_id)

    primary_asset_id = uploaded_ids[0]
    client.add_assets_to_album(album_id, uploaded_ids)

    if settings.wiggle_stack_with_sources:
        source_ids = [asset.asset_id for asset in group.assets]
        client.create_stack([primary_asset_id, *source_ids])

    store.mark_exported(group.group_key, primary_asset_id)
    return True


def export_groups(
    settings: Settings,
    store: HashStore,
    groups: list[WiggleGroup],
    *,
    byte_cache: ImageByteCache | None = None,
) -> ExportSummary:
    summary = ExportSummary()
    if not groups:
        return summary

    cache = byte_cache or byte_cache_from_settings(settings)
    pending = [group for group in groups if not store.is_exported(group.group_key)]
    summary.skipped += len(groups) - len(pending)

    if not pending:
        return summary

    with ImmichClient(settings.immich_base_url, settings.immich_api_key) as client:
        album = client.get_or_create_album(settings.wiggle_album_name)
        album_id = album["id"]

        if settings.export_workers <= 1 or len(pending) <= 1:
            for group in pending:
                try:
                    if _export_single_group(
                        client,
                        store,
                        settings,
                        group,
                        album_id=album_id,
                        byte_cache=cache,
                    ):
                        summary.exported += 1
                except ImmichError:
                    summary.errors += 1
            return summary

        errors_lock = threading.Lock()

        def _export_group(group: WiggleGroup) -> bool:
            try:
                return _export_single_group(
                    client,
                    store,
                    settings,
                    group,
                    album_id=album_id,
                    byte_cache=cache,
                )
            except ImmichError:
                with errors_lock:
                    summary.errors += 1
                return False

        with ThreadPoolExecutor(max_workers=settings.export_workers) as executor:
            futures = [executor.submit(_export_group, group) for group in pending]
            for future in as_completed(futures):
                if future.result():
                    summary.exported += 1

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
    byte_cache = byte_cache_from_settings(settings)
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
            byte_cache=byte_cache,
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
            summary = export_groups(settings, store, pending, byte_cache=byte_cache)
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
