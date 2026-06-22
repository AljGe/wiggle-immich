from __future__ import annotations

from datetime import datetime

import imagehash
import pytest

from image_helper.config import Settings
from image_helper.detector import find_wiggle_groups
from image_helper.group_validation import partition_wiggle_groups, validate_wiggle_group
from image_helper.models import AssetRecord, WiggleGroup
from image_helper.service import detect_groups_with_validation


def _settings(**overrides) -> Settings:
    values = {
        "IMMICH_URL": "http://immich.test/api",
        "IMMICH_API_KEY": "test-key",
    }
    values.update(overrides)
    return Settings(**values)


def _record(
    asset_id: str,
    dt: str,
    phash: str,
    *,
    width: int | None = 1000,
    height: int | None = 800,
    stack_id: str | None = None,
    original_file_name: str | None = None,
) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        local_datetime=datetime.fromisoformat(dt),
        phash=phash,
        width=width,
        height=height,
        stack_id=stack_id,
        original_file_name=original_file_name,
    )


def _similar_phash(seed: str, *, flips: int = 1) -> str:
    base = imagehash.hex_to_hash(seed)
    bits = base.hash.copy()
    for index in range(flips):
        bits.flat[index] = not bits.flat[index]
    return str(imagehash.ImageHash(bits))


def test_validate_rejects_shared_stack_members() -> None:
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed, stack_id="stack-1"),
            _record("b", "2026-01-01T12:00:01+00:00", _similar_phash(seed), stack_id="stack-1"),
        ),
        distances=(1,),
    )
    reason = validate_wiggle_group(group, _settings())
    assert reason == "members share an Immich stack"


def test_validate_rejects_dimension_drift() -> None:
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed, width=1000, height=800),
            _record(
                "b",
                "2026-01-01T12:00:01+00:00",
                _similar_phash(seed, flips=3),
                width=700,
                height=800,
            ),
        ),
        distances=(3,),
    )
    reason = validate_wiggle_group(group, _settings())
    assert reason is not None
    assert "dimension drift" in reason


def test_validate_rejects_edit_like_identical_timestamps() -> None:
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed),
            _record("b", "2026-01-01T12:00:00+00:00", _similar_phash(seed, flips=1)),
        ),
        distances=(1,),
    )
    reason = validate_wiggle_group(group, _settings(WIGGLE_MIN_DISTANCE=2))
    assert reason == "edit-like similarity with non-progressive timestamps"


def test_validate_accepts_progressive_burst() -> None:
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed),
            _record("b", "2026-01-01T12:00:01+00:00", _similar_phash(seed, flips=4)),
            _record("c", "2026-01-01T12:00:02+00:00", _similar_phash(seed, flips=8)),
        ),
        distances=(4, 4),
    )
    assert validate_wiggle_group(group, _settings()) is None


def test_detect_groups_with_validation_filters_false_positives() -> None:
    seed = "0" * 16
    burst_seed = "f" * 16
    assets = [
        _record("edit-a", "2026-01-01T12:00:00+00:00", seed, width=1000, height=800),
        _record(
            "edit-b",
            "2026-01-01T12:00:00+00:00",
            _similar_phash(seed, flips=1),
            width=700,
            height=800,
        ),
        _record("burst-a", "2026-01-01T12:01:00+00:00", burst_seed),
        _record("burst-b", "2026-01-01T12:01:01+00:00", _similar_phash(burst_seed, flips=4)),
        _record("burst-c", "2026-01-01T12:01:02+00:00", _similar_phash(burst_seed, flips=8)),
    ]
    raw_groups = find_wiggle_groups(assets, threshold=10, time_window_seconds=3.0)
    assert len(raw_groups) == 2

    result = detect_groups_with_validation(_settings(), assets)
    assert len(result.accepted) == 1
    assert [asset.asset_id for asset in result.accepted[0].assets] == ["burst-a", "burst-b", "burst-c"]
    assert len(result.rejected) == 1
    assert [asset.asset_id for asset in result.rejected[0].group.assets] == ["edit-a", "edit-b"]


def test_validate_rejects_missing_burst_metadata_when_required() -> None:
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            AssetRecord(
                asset_id="a",
                local_datetime=datetime.fromisoformat("2026-01-01T12:00:00+00:00"),
                phash=seed,
                burst_id="burst-1",
            ),
            AssetRecord(
                asset_id="b",
                local_datetime=datetime.fromisoformat("2026-01-01T12:00:01+00:00"),
                phash=_similar_phash(seed, flips=4),
            ),
        ),
        distances=(4,),
    )
    reason = validate_wiggle_group(group, _settings(WIGGLE_REQUIRE_BURST_METADATA=True))
    assert reason == "missing burst metadata"


def test_validate_rejects_exported_wiggle_assets() -> None:
    seed = "0" * 16
    group = WiggleGroup(
        assets=(
            _record("a", "2026-01-01T12:00:00+00:00", seed),
            _record(
                "b",
                "2026-01-01T12:00:01+00:00",
                _similar_phash(seed, flips=4),
                original_file_name="wiggle_2026-01-01_12-00-00.gif",
            ),
        ),
        distances=(4,),
    )
    reason = validate_wiggle_group(group, _settings())
    assert reason == "contains previously exported wiggle asset"
