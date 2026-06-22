from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx
import imagehash
import pillow_heif
from PIL import Image

pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"IMAGE"}


class ImmichError(Exception):
    """Raised when an Immich API request fails."""


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "x-api-key": api_key,
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ImmichClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.is_error:
            detail = response.text[:500]
            raise ImmichError(f"{method} {path} failed ({response.status_code}): {detail}")
        return response

    def search_images(
        self,
        *,
        taken_after: datetime | None = None,
        taken_before: datetime | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        size: int = 250,
        order: str = "asc",
    ) -> Iterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "type": "IMAGE",
            "size": size,
            "order": order,
            "withExif": False,
        }
        if taken_after is not None:
            payload["takenAfter"] = _to_iso(taken_after)
        if taken_before is not None:
            payload["takenBefore"] = _to_iso(taken_before)
        if updated_after is not None:
            payload["updatedAfter"] = _to_iso(updated_after)
        if updated_before is not None:
            payload["updatedBefore"] = _to_iso(updated_before)

        page = 1
        while True:
            body = dict(payload)
            body["page"] = page

            response = self._request("POST", "/search/metadata", json=body)
            data = response.json()
            assets = data.get("assets", {})
            items = assets.get("items", [])
            for item in items:
                if item.get("type") in IMAGE_TYPES:
                    yield item

            next_page = assets.get("nextPage")
            if not next_page or not items:
                break
            page = int(next_page)

    def iter_all_images(self, *, batch_size: int = 250) -> Iterator[dict[str, Any]]:
        yield from self.search_images(size=batch_size, order="asc")

    def search_neighbors(
        self,
        center: datetime,
        *,
        window_seconds: float,
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        half = timedelta(seconds=window_seconds / 2)
        taken_after = center - half
        taken_before = center + half
        assets = list(
            self.search_images(
                taken_after=taken_after,
                taken_before=taken_before,
                size=batch_size,
                order="asc",
            )
        )
        assets.sort(key=lambda asset: asset["localDateTime"])
        return assets

    def download_thumbnail(self, asset_id: str, *, size: str = "preview") -> bytes:
        response = self._request(
            "GET",
            f"/assets/{asset_id}/thumbnail",
            params={"size": size},
        )
        return response.content

    def download_original(self, asset_id: str) -> bytes:
        response = self._request("GET", f"/assets/{asset_id}/original")
        return response.content

    def compute_phash_from_bytes(self, data: bytes) -> str:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            rgb = image.convert("RGB")
            return str(imagehash.phash(rgb))

    def hash_asset_thumbnail(self, asset_id: str) -> str:
        data = self.download_thumbnail(asset_id)
        return self.compute_phash_from_bytes(data)

    def upload_gif(
        self,
        gif_bytes: bytes,
        *,
        filename: str,
        file_created_at: datetime,
        device_asset_id: str,
        device_id: str,
    ) -> dict[str, Any]:
        files = {"assetData": (filename, gif_bytes, "image/gif")}
        data = {
            "deviceAssetId": device_asset_id,
            "deviceId": device_id,
            "fileCreatedAt": _to_iso(file_created_at),
            "fileModifiedAt": _to_iso(file_created_at),
            "filename": filename,
        }
        response = self._request("POST", "/assets", files=files, data=data)
        return response.json()

    def list_albums(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/albums")
        return response.json()

    def create_album(self, album_name: str) -> dict[str, Any]:
        response = self._request("POST", "/albums", json={"albumName": album_name})
        return response.json()

    def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        self._request("PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids})

    def get_or_create_album(self, album_name: str) -> dict[str, Any]:
        for album in self.list_albums():
            if album.get("albumName") == album_name:
                return album
        return self.create_album(album_name)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_local_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)
