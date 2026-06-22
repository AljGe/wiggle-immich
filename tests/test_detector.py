from datetime import datetime

import imagehash
import pytest

from image_helper.detector import find_wiggle_groups, phash_distance
from image_helper.models import AssetRecord


def _record(asset_id: str, dt: str, phash: str) -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        local_datetime=datetime.fromisoformat(dt),
        phash=phash,
    )


def _similar_phash(seed: str, *, flips: int = 2) -> str:
    base = imagehash.hex_to_hash(seed)
    bits = base.hash.copy()
    for index in range(flips):
        bits.flat[index] = not bits.flat[index]
    return str(imagehash.ImageHash(bits))


def test_phash_distance_zero_for_identical() -> None:
    value = "0" * 16
    assert phash_distance(value, value) == 0


def test_find_wiggle_groups_groups_adjacent_similar_frames() -> None:
    seed = "0" * 16
    hash_b = _similar_phash(seed, flips=2)
    hash_c = _similar_phash(hash_b, flips=2)
    assets = [
        _record("a", "2026-01-01T12:00:00+00:00", seed),
        _record("b", "2026-01-01T12:00:01+00:00", hash_b),
        _record("c", "2026-01-01T12:00:02+00:00", hash_c),
        _record("d", "2026-01-01T12:05:00+00:00", "f" * 16),
    ]

    groups = find_wiggle_groups(assets, threshold=10, time_window_seconds=3.0)
    assert len(groups) == 1
    assert [asset.asset_id for asset in groups[0].assets] == ["a", "b", "c"]


def test_find_wiggle_groups_respects_time_window() -> None:
    seed = "0" * 16
    assets = [
        _record("a", "2026-01-01T12:00:00+00:00", seed),
        _record("b", "2026-01-01T12:00:10+00:00", _similar_phash(seed)),
    ]

    groups = find_wiggle_groups(assets, threshold=10, time_window_seconds=3.0)
    assert groups == []
