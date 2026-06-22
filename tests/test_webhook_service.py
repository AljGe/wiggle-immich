from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from image_helper.config import Settings
from image_helper.hashstore import HashStore
from image_helper.service import process_webhook_asset_id


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        IMMICH_URL="http://immich.test/api",
        IMMICH_API_KEY="test-key",
        HASH_DB_PATH=tmp_path / "hashes.sqlite3",
    )


def test_process_webhook_asset_id_hydrates_missing_local_datetime(
    settings: Settings,
    tmp_path,
) -> None:
    store = HashStore(settings.hash_db_path)
    full_asset = {
        "id": "asset-1",
        "localDateTime": "2026-01-01T12:00:00+00:00",
        "checksum": "abc",
    }

    client = MagicMock()
    client.get_asset.return_value = full_asset
    client.search_neighbors.return_value = []
    client.__enter__.return_value = client
    client.__exit__.return_value = None

    with patch("image_helper.service.ImmichClient", return_value=client):
        result = process_webhook_asset_id(
            settings,
            store,
            "asset-1",
            raw_asset={"id": "asset-1"},
            trigger="AssetCreate",
        )

    client.get_asset.assert_called_once_with("asset-1")
    client.wait_for_thumbnail.assert_called_once_with("asset-1")
    assert result["resolved_asset"] is True
    assert result["trigger"] == "AssetCreate"


def test_webhook_endpoint_accepts_async_mode(settings: Settings, tmp_path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from image_helper.webhook import create_app

    settings = Settings(
        IMMICH_URL="http://immich.test/api",
        IMMICH_API_KEY="test-key",
        HASH_DB_PATH=tmp_path / "hashes.sqlite3",
        WEBHOOK_SECRET="secret",
        WEBHOOK_ASYNC=True,
    )

    with patch("image_helper.webhook.WebhookJobQueue") as queue_cls:
        queue_instance = MagicMock()
        queue_cls.return_value = queue_instance
        app = create_app(settings)
        client = TestClient(app)

        response = client.post(
            "/webhook/immich",
            json={"asset": {"id": "asset-1"}},
            headers={"x-immich-webhook-secret": "secret"},
        )

    assert response.status_code == 202
    queue_instance.submit.assert_called_once()
    assert response.json()["status"] == "accepted"


def test_webhook_endpoint_accepts_id_only_payload(settings: Settings, tmp_path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from image_helper.webhook import create_app

    settings = Settings(
        IMMICH_URL="http://immich.test/api",
        IMMICH_API_KEY="test-key",
        HASH_DB_PATH=tmp_path / "hashes.sqlite3",
        WEBHOOK_SECRET="secret",
        WEBHOOK_ASYNC=True,
    )

    with patch("image_helper.webhook.WebhookJobQueue") as queue_cls:
        queue_instance = MagicMock()
        queue_cls.return_value = queue_instance
        app = create_app(settings)
        client = TestClient(app)

        with patch("image_helper.webhook.process_webhook_asset_id") as mocked:
            response = client.post(
                "/webhook/immich",
                json={"asset": {"id": "asset-1"}},
                headers={"x-immich-webhook-secret": "secret"},
            )

    assert response.status_code == 202
    queue_instance.submit.assert_called_once()
    mocked.assert_not_called()
    assert response.json()["asset_id"] == "asset-1"
    assert response.json()["status"] == "accepted"


def test_webhook_endpoint_sync_mode_processes_inline(settings: Settings, tmp_path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from image_helper.webhook import create_app

    settings = Settings(
        IMMICH_URL="http://immich.test/api",
        IMMICH_API_KEY="test-key",
        HASH_DB_PATH=tmp_path / "hashes.sqlite3",
        WEBHOOK_SECRET="secret",
        WEBHOOK_ASYNC=False,
    )
    app = create_app(settings)
    client = TestClient(app)

    with patch(
        "image_helper.webhook.process_webhook_asset_id",
        return_value={
            "trigger": "AssetCreate",
            "resolved_asset": True,
            "indexed_neighbors": False,
            "groups_found": 0,
            "exported": 0,
        },
    ) as mocked:
        response = client.post(
            "/webhook/immich",
            json={"asset": {"id": "asset-1"}},
            headers={"x-immich-webhook-secret": "secret"},
        )

    assert response.status_code == 200
    mocked.assert_called_once()
    assert response.json()["asset_id"] == "asset-1"
    assert response.json()["resolved_asset"] is True
