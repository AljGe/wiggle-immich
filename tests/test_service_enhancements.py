from __future__ import annotations

from datetime import datetime, timedelta, timezone

import imagehash
import pytest

from image_helper.config import Settings
from image_helper.group_validation import validate_wiggle_group
from image_helper.hashstore import HashStore
from image_helper.models import AssetRecord, WiggleGroup
from image_helper.service import (
    DetectResult,
    apply_settle_filter,
    collect_export_candidates,
    detect_groups_for_centers,
    filter_ready_groups,
    merge_detect_results,
    run_daemon_detection,
)


def _similar_phash(seed: str, *, flips: int = 4) -> str:
    base = imagehash.hex_to_hash(seed)
    bits = base.hash.copy()
    for index in range(flips):
        bits.flat[index] = not bits.flat[index]
    return str(imagehash.ImageHash(bits))


def _settings(**overrides) -> Settings:
    values = {
        "IMMICH_URL": "http://immich.test/api",
        "IMMICH_API_KEY": "test-key",
    }
    values.update(overrides)
    return Settings(**values)


def _record(asset_id: str, dt: str, phash: str = "0" * 16) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        local_datetime=datetime.fromisoformat(dt),
        phash=phash,
        width=1000,
        height=800,
    )


def test_merge_detect_results_dedupes_by_group_key() -> None:
    group = WiggleGroup(
        assets=(_record("a", "2026-01-01T12:00:00+00:00"), _record("b", "2026-01-01T12:00:01+00:00")),
        distances=(3,),
    )
    merged = merge_detect_results(
        [
            DetectResult(accepted=[group], rejected=[]),
            DetectResult(accepted=[group], rejected=[]),
        ]
    )
    assert len(merged.accepted) == 1


def test_detect_groups_for_centers_limits_scan(tmp_path) -> None:
    center = datetime.fromisoformat("2026-01-01T12:00:00+00:00")
    store = HashStore(tmp_path / "hashes.sqlite3")
    store.upsert_many(
        [
            _record("near-a", "2026-01-01T12:00:00+00:00"),
            _record("near-b", "2026-01-01T12:00:01+00:00", phash="1" * 16),
            _record("far", "2026-01-01T12:10:00+00:00", phash="2" * 16),
        ]
    )

    result = detect_groups_for_centers(_settings(), store, [center])
    considered_ids = {
        asset.asset_id
        for group in result.accepted
        for asset in group.assets
    } | {
        asset.asset_id
        for entry in result.rejected
        for asset in entry.group.assets
    }
    assert "far" not in considered_ids


def test_apply_settle_filter_defers_recent_groups(tmp_path) -> None:
    store = HashStore(tmp_path / "hashes.sqlite3")
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed),
            _record("b", "2026-01-01T12:00:01+00:00", _similar_phash(seed)),
        ),
        distances=(4,),
    )
    now = datetime.now(timezone.utc)

    ready = apply_settle_filter(_settings(WIGGLE_SETTLE_SECONDS=30), store, [group], now=now)
    assert ready == []

    store.touch_pending_group(group.group_key, seen_at=now - timedelta(seconds=60))
    ready = filter_ready_groups(store, [group], settle_seconds=30, now=now)
    assert len(ready) == 1


def test_run_daemon_detection_returns_settled_groups_without_new_index(tmp_path) -> None:
    store = HashStore(tmp_path / "hashes.sqlite3")
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed),
            _record("b", "2026-01-01T12:00:01+00:00", _similar_phash(seed)),
        ),
        distances=(4,),
    )
    store.upsert_many(list(group.assets))
    store.touch_pending_group(group.group_key, seen_at=datetime.now(timezone.utc) - timedelta(seconds=60))

    result = run_daemon_detection(_settings(WIGGLE_SETTLE_SECONDS=30), store, [])
    assert len(result.accepted) == 1
    assert result.accepted[0].group_key == group.group_key


def test_validate_rejects_mixed_burst_ids() -> None:
    group = WiggleGroup(
        assets=(
            AssetRecord(
                asset_id="a",
                local_datetime=datetime.fromisoformat("2026-01-01T12:00:00+00:00"),
                phash="0" * 16,
                burst_id="burst-1",
            ),
            AssetRecord(
                asset_id="b",
                local_datetime=datetime.fromisoformat("2026-01-01T12:00:01+00:00"),
                phash="1" * 16,
                burst_id="burst-2",
            ),
        ),
        distances=(4,),
    )
    reason = validate_wiggle_group(group, _settings())
    assert reason == "mixed burst identifiers"


def test_collect_export_candidates_skips_exported(tmp_path) -> None:
    store = HashStore(tmp_path / "hashes.sqlite3")
    group = WiggleGroup(
        assets=(_record("a", "2026-01-01T12:00:00+00:00"), _record("b", "2026-01-01T12:00:01+00:00", phash="1" * 16)),
        distances=(4,),
    )
    store.mark_exported(group.group_key, "gif-1")

    pending = collect_export_candidates(_settings(), store, [group])
    assert pending == []
