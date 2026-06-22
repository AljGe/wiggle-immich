"""Webhook receiver for Immich workflow triggers."""

from __future__ import annotations

import logging
from typing import Any

from image_helper.config import Settings
from image_helper.hashstore import HashStore
from image_helper.service import process_webhook_asset_id
from image_helper.workflow_payload import WebhookPayloadError, normalize_webhook_payload

logger = logging.getLogger(__name__)


def create_app(settings: Settings):
    from fastapi import FastAPI, Header, HTTPException

    app = FastAPI(
        title="image-helper webhook",
        description="Receives Immich workflow webhook payloads.",
        version="0.1.0",
    )
    store = HashStore(settings.hash_db_path)

    def _verify_secret(secret: str | None) -> None:
        if settings.webhook_secret and secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook/immich")
    async def immich_webhook(
        payload: dict[str, Any],
        x_immich_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _verify_secret(x_immich_webhook_secret)

        try:
            event = normalize_webhook_payload(payload)
        except WebhookPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        logger.info(
            "Webhook received trigger=%s asset=%s",
            event.trigger,
            event.asset_id,
        )

        result = process_webhook_asset_id(
            settings,
            store,
            event.asset_id,
            raw_asset=event.raw_asset,
            trigger=event.trigger,
        )
        return {
            "status": "processed",
            "asset_id": event.asset_id,
            **result,
        }

    return app


def run_webhook_server(settings: Settings, *, host: str, port: int) -> None:
    import uvicorn

    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_level="info")
