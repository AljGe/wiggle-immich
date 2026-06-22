#!/usr/bin/env python3
"""Bootstrap Immich for E2E tests: admin, API key, fixture upload."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

REQUIRED_PERMISSIONS = [
    "asset.read",
    "asset.view",
    "asset.download",
    "asset.upload",
    "album.read",
    "album.create",
    "albumAsset.create",
]


def wait_for_immich(base_url: str, timeout_seconds: int = 300) -> None:
    deadline = time.time() + timeout_seconds
    ping_url = f"{base_url.rstrip('/')}/server/ping"
    print(f"Waiting for Immich at {ping_url} ...")
    while time.time() < deadline:
        try:
            response = httpx.get(ping_url, timeout=5)
            if response.status_code == 200:
                print("Immich is ready.")
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise TimeoutError(f"Immich did not become ready within {timeout_seconds}s")


def ensure_admin(base_url: str, email: str, password: str, name: str) -> None:
    response = httpx.post(
        f"{base_url}/auth/admin-sign-up",
        json={"email": email, "password": password, "name": name},
        timeout=30,
    )
    if response.status_code in (200, 201):
        print("Created admin user.")
        return
    if response.status_code == 400 and "already" in response.text.lower():
        print("Admin user already exists.")
        return
    response.raise_for_status()


def login(base_url: str, email: str, password: str) -> str:
    response = httpx.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    token = response.json()["accessToken"]
    print("Logged in.")
    return token


def create_api_key(base_url: str, access_token: str, name: str) -> str:
    response = httpx.post(
        f"{base_url}/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": name, "permissions": REQUIRED_PERMISSIONS},
        timeout=30,
    )
    response.raise_for_status()
    secret = response.json()["secret"]
    print("Created API key.")
    return secret


def upload_fixture(
    base_url: str,
    api_key: str,
    path: Path,
    *,
    captured_at: datetime,
    device_asset_id: str,
) -> str:
    with path.open("rb") as handle:
        files = {"assetData": (path.name, handle, "image/png")}
        data = {
            "deviceAssetId": device_asset_id,
            "deviceId": "image-helper-e2e",
            "fileCreatedAt": _iso(captured_at),
            "fileModifiedAt": _iso(captured_at),
            "filename": path.name,
        }
        response = httpx.post(
            f"{base_url}/assets",
            headers={"x-api-key": api_key},
            files=files,
            data=data,
            timeout=120,
        )
    response.raise_for_status()
    asset_id = response.json()["id"]
    print(f"Uploaded {path.name} -> {asset_id}")
    return asset_id


def wait_for_thumbnail(base_url: str, api_key: str, asset_id: str, timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    url = f"{base_url}/assets/{asset_id}/thumbnail"
    while time.time() < deadline:
        response = httpx.get(
            url,
            headers={"x-api-key": api_key},
            params={"size": "preview"},
            timeout=30,
        )
        if response.status_code == 200 and response.content:
            print(f"Thumbnail ready for {asset_id}")
            return
        time.sleep(2)
    raise TimeoutError(f"Thumbnail not ready for {asset_id}")


def write_runtime_env(path: Path, api_key: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"IMMICH_API_KEY={api_key}",
                "WIGGLE_THRESHOLD=12",
                "WIGGLE_TIME_WINDOW_SECONDS=5",
                "WIGGLE_ALBUM_NAME=Wigglegrams",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Wrote runtime env to {path}")


def upload_fixtures(base_url: str, api_key: str, fixtures_dir: Path) -> list[str]:
    burst_files = sorted(fixtures_dir.glob("wiggle_burst_*.png"))
    if len(burst_files) < 2:
        raise RuntimeError(f"Expected burst fixtures in {fixtures_dir}")

    base_time = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    asset_ids: list[str] = []

    for index, path in enumerate(burst_files):
        asset_id = upload_fixture(
            base_url,
            api_key,
            path,
            captured_at=base_time + timedelta(seconds=index),
            device_asset_id=f"e2e-burst-{path.stem}",
        )
        wait_for_thumbnail(base_url, api_key, asset_id)
        asset_ids.append(asset_id)

    control = fixtures_dir / "control_unrelated.png"
    if control.exists():
        control_id = upload_fixture(
            base_url,
            api_key,
            control,
            captured_at=base_time + timedelta(minutes=10),
            device_asset_id="e2e-control",
        )
        wait_for_thumbnail(base_url, api_key, control_id)
        asset_ids.append(control_id)

    return asset_ids


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--immich-url", default="http://localhost:2283/api")
    parser.add_argument("--admin-email", default="admin@image-helper.test")
    parser.add_argument("--admin-password", default="image-helper-test")
    parser.add_argument("--admin-name", default="E2E Admin")
    parser.add_argument("--fixtures-dir", type=Path, default=Path("testdata/fixtures"))
    parser.add_argument(
        "--runtime-env",
        type=Path,
        default=Path("docker/test.runtime.env"),
        help="Path for generated image-helper env file",
    )
    args = parser.parse_args()

    wait_for_immich(args.immich_url)
    ensure_admin(args.immich_url, args.admin_email, args.admin_password, args.admin_name)
    token = login(args.immich_url, args.admin_email, args.admin_password)
    api_key = create_api_key(args.immich_url, token, "image-helper-e2e")
    upload_fixtures(args.immich_url, api_key, args.fixtures_dir)
    write_runtime_env(args.runtime_env, api_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
