from __future__ import annotations

from datetime import datetime, timedelta

from image_helper.config import Settings
from image_helper.hashstore import HashStore
from image_helper.models import AssetRecord
from image_helper.service import detect_groups_in_range


def test_detect_groups_in_range_limits_candidates(tmp_path) -> None:
    center = datetime.fromisoformat("2026-01-01T12:00:00+00:00")
    store = HashStore(tmp_path / "hashes.sqlite3")
    store.upsert_many(
        [
            AssetRecord(
                asset_id="near-a",
                local_datetime=center,
                phash="0" * 16,
                width=1000,
                height=800,
            ),
            AssetRecord(
                asset_id="near-b",
                local_datetime=center + timedelta(seconds=1),
                phash="1" * 16,
                width=1000,
                height=800,
            ),
            AssetRecord(
                asset_id="far",
                local_datetime=center + timedelta(minutes=5),
                phash="2" * 16,
                width=1000,
                height=800,
            ),
        ]
    )

    settings = Settings(
        IMMICH_URL="http://immich.test/api",
        IMMICH_API_KEY="test-key",
        HASH_DB_PATH=tmp_path / "hashes.sqlite3",
    )
    in_range = store.list_in_range(
        center - timedelta(seconds=30),
        center + timedelta(seconds=30),
    )
    assert {record.asset_id for record in in_range} == {"near-a", "near-b"}

    result = detect_groups_in_range(
        settings,
        store,
        center=center,
        window_seconds=30,
    )
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
