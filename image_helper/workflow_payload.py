from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class WebhookPayloadError(ValueError):
    """Raised when an Immich workflow webhook payload is invalid."""


@dataclass(frozen=True)
class NormalizedEvent:
    trigger: str | None
    asset_id: str
    raw_asset: dict[str, Any]


def normalize_webhook_payload(body: dict[str, Any]) -> NormalizedEvent:
    if not isinstance(body, dict):
        raise WebhookPayloadError("Payload must be a JSON object")

    asset = body.get("asset")
    if not isinstance(asset, dict):
        raise WebhookPayloadError("Payload must include asset object")

    asset_id = asset.get("id")
    if not asset_id:
        raise WebhookPayloadError("Payload must include asset.id")

    trigger = body.get("trigger")
    if trigger is None and isinstance(body.get("type"), str):
        trigger = body["type"]

    return NormalizedEvent(
        trigger=str(trigger) if trigger is not None else None,
        asset_id=str(asset_id),
        raw_asset=asset,
    )
