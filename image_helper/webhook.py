"""Phase 2 webhook receiver stub for Immich workflow triggers."""

from __future__ import annotations

import logging
from typing import Any

from image_helper.config import Settings
from image_helper.hashstore import HashStore
from image_helper.service import process_webhook_asset

logger = logging.getLogger(__name__)


def create_app(settings: Settings):
    from fastapi import FastAPI, Header, HTTPException, Request

    app = FastAPI(
        title="image-helper webhook",
        description="Receives Immich workflow webhook payloads (Phase 2 stub).",
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
        request: Request,
        x_immich_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _verify_secret(x_immich_webhook_secret)
        payload = await request.json()

        trigger = payload.get("trigger")
        asset = payload.get("asset")
        if not asset or "id" not in asset:
            raise HTTPException(status_code=400, detail="Payload must include asset.id")

        asset_id = asset["id"]
        logger.info("Webhook received trigger=%s asset=%s", trigger, asset_id)

        result = process_webhook_asset(settings, store, asset)
        return {
            "status": "processed",
            "asset_id": asset_id,
            **result,
        }

    return app


def run_webhook_server(settings: Settings, *, host: str, port: int) -> None:
    import uvicorn

    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_level="info")
