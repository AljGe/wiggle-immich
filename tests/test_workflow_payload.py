from __future__ import annotations

import pytest

from image_helper.workflow_payload import WebhookPayloadError, normalize_webhook_payload


def test_normalize_asset_create_shape() -> None:
    event = normalize_webhook_payload(
        {
            "trigger": "AssetCreate",
            "asset": {"id": "asset-1", "localDateTime": "2026-01-01T12:00:00Z"},
        }
    )
    assert event.trigger == "AssetCreate"
    assert event.asset_id == "asset-1"


def test_normalize_id_only_asset() -> None:
    event = normalize_webhook_payload({"asset": {"id": "asset-2"}})
    assert event.trigger is None
    assert event.asset_id == "asset-2"


def test_normalize_legacy_type_field() -> None:
    event = normalize_webhook_payload(
        {
            "type": "AssetCreationEvent",
            "asset": {"id": "asset-3"},
        }
    )
    assert event.trigger == "AssetCreationEvent"
    assert event.asset_id == "asset-3"


def test_missing_asset_id_raises() -> None:
    with pytest.raises(WebhookPayloadError, match="asset.id"):
        normalize_webhook_payload({"asset": {}})
