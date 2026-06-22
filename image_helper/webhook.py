"""Webhook receiver for Immich workflow triggers."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from image_helper.config import Settings
from image_helper.hashstore import HashStore
from image_helper.service import process_webhook_asset_id
from image_helper.workflow_payload import WebhookPayloadError, normalize_webhook_payload

logger = logging.getLogger(__name__)


class WebhookJobQueue:
    def __init__(
        self,
        settings: Settings,
        store: HashStore,
        *,
        maxsize: int,
    ) -> None:
        self._settings = settings
        self._store = store
        self._queue: queue.Queue[tuple[str, dict[str, Any] | None, str | None] | None] = queue.Queue(
            maxsize=maxsize
        )
        self._worker = threading.Thread(target=self._run, name="image-helper-webhook", daemon=True)
        self._worker.start()

    def submit(
        self,
        asset_id: str,
        *,
        raw_asset: dict[str, Any] | None,
        trigger: str | None,
    ) -> None:
        self._queue.put((asset_id, raw_asset, trigger), block=True)

    def stop(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=5)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                asset_id, raw_asset, trigger = job
                result = process_webhook_asset_id(
                    self._settings,
                    self._store,
                    asset_id,
                    raw_asset=raw_asset,
                    trigger=trigger,
                )
                logger.info(
                    "Webhook job complete asset=%s exported=%s groups=%s",
                    asset_id,
                    result.get("exported"),
                    result.get("groups_found"),
                )
            except Exception:
                logger.exception("Webhook job failed")
            finally:
                self._queue.task_done()


def create_app(settings: Settings):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import JSONResponse

    app = FastAPI(
        title="image-helper webhook",
        description="Receives Immich workflow webhook payloads.",
        version="0.1.0",
    )
    store = HashStore(settings.hash_db_path)
    job_queue = (
        WebhookJobQueue(settings, store, maxsize=settings.webhook_queue_size)
        if settings.webhook_async
        else None
    )

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
    ):
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

        if job_queue is not None:
            job_queue.submit(
                event.asset_id,
                raw_asset=event.raw_asset,
                trigger=event.trigger,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "accepted",
                    "asset_id": event.asset_id,
                    "trigger": event.trigger,
                },
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
