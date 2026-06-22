#!/usr/bin/env python3
"""Verify image-helper E2E results against a running Immich test stack."""

from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--immich-url", default="http://localhost:2283/api")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--album-name", default="Wigglegrams")
    args = parser.parse_args()

    headers = {"x-api-key": args.api_key, "Accept": "application/json"}

    albums = httpx.get(f"{args.immich_url}/albums", headers=headers, timeout=30)
    albums.raise_for_status()
    target = next((album for album in albums.json() if album.get("albumName") == args.album_name), None)
    if target is None:
        print(f"FAIL: album '{args.album_name}' not found")
        return 1

    album = httpx.get(f"{args.immich_url}/albums/{target['id']}", headers=headers, timeout=30)
    album.raise_for_status()
    album_data = album.json()
    assets = album_data.get("assets", [])
    gif_assets = [
        asset
        for asset in assets
        if str(asset.get("originalFileName", "")).startswith("wiggle_")
        or str(asset.get("originalMimeType", "")).endswith("gif")
    ]
    if not gif_assets:
        print(f"FAIL: album '{args.album_name}' has no wiggle GIF assets")
        return 1

    print(
        f"PASS: album '{args.album_name}' contains {len(gif_assets)} wiggle GIF asset(s) "
        f"(album asset count: {album_data.get('assetCount', len(assets))})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
