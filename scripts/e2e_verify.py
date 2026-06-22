#!/usr/bin/env python3
"""Verify image-helper E2E results against a running Immich test stack."""

from __future__ import annotations

import argparse
import sys

import httpx

from e2e_album import album_contains_wigglegram


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
    if not album_contains_wigglegram(
        album_data,
        immich_url=args.immich_url,
        api_key=args.api_key,
    ):
        print(f"FAIL: album '{args.album_name}' has no wiggle GIF assets")
        return 1

    asset_count = album_data.get("assetCount", len(album_data.get("assets", [])))
    print(f"PASS: album '{args.album_name}' contains wiggle GIF asset(s) (asset count: {asset_count})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
