"""Shared album checks for E2E scripts (Immich v2 + v3 album response shapes)."""

from __future__ import annotations

import httpx


def _is_wiggle_asset(asset: dict) -> bool:
    return str(asset.get("originalFileName", "")).startswith("wiggle_") or str(
        asset.get("originalMimeType", "")
    ).endswith("gif")


def album_contains_wigglegram(
    album_data: dict,
    *,
    immich_url: str,
    api_key: str,
) -> bool:
    assets = album_data.get("assets")
    if isinstance(assets, list) and assets:
        return any(_is_wiggle_asset(asset) for asset in assets)

    if int(album_data.get("assetCount", 0) or 0) <= 0:
        return False

    thumb_id = album_data.get("albumThumbnailAssetId")
    if not thumb_id:
        return True

    headers = {"x-api-key": api_key, "Accept": "application/json"}
    response = httpx.get(f"{immich_url.rstrip('/')}/assets/{thumb_id}", headers=headers, timeout=30)
    if not response.is_success:
        return False
    return _is_wiggle_asset(response.json())
