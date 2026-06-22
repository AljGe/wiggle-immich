#!/usr/bin/env python3
"""Bootstrap Immich workflow E2E: workflow + sequential fixture uploads."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from image_helper.immich_workflows import (
    WIGGLEGRAM_WORKFLOW_NAME,
    discover_webhook_method,
    ensure_wigglegram_workflow,
    probe_workflows,
)

# Reuse Immich bootstrap helpers from REST E2E.
from e2e_bootstrap import (
    create_api_key,
    ensure_admin,
    login,
    upload_fixture,
    wait_for_immich,
    wait_for_thumbnail,
)

DEFAULT_WEBHOOK_URL = "http://image-helper-webhook:8765/webhook/immich"
DEFAULT_WEBHOOK_SECRET = "image-helper-e2e-secret"


def write_runtime_env(path: Path, api_key: str, *, webhook_secret: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"IMMICH_API_KEY={api_key}",
                f"WEBHOOK_SECRET={webhook_secret}",
                "WIGGLE_THRESHOLD=12",
                "WIGGLE_TIME_WINDOW_SECONDS=5",
                "WIGGLE_ALBUM_NAME=Wigglegrams",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Wrote runtime env to {path}")


def load_api_key_from_env(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("IMMICH_API_KEY="):
            return line.split("=", 1)[1]
    raise RuntimeError(f"IMMICH_API_KEY not found in {path}")


def upload_burst_fixtures_sequential(
    base_url: str,
    api_key: str,
    fixtures_dir: Path,
    *,
    delay_seconds: float = 1.0,
) -> list[str]:
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
            device_asset_id=f"e2e-workflow-burst-{path.stem}",
        )
        wait_for_thumbnail(base_url, api_key, asset_id)
        asset_ids.append(asset_id)
        if delay_seconds:
            time.sleep(delay_seconds)

    control = fixtures_dir / "control_unrelated.png"
    if control.exists():
        control_id = upload_fixture(
            base_url,
            api_key,
            control,
            captured_at=base_time + timedelta(minutes=10),
            device_asset_id="e2e-workflow-control",
        )
        wait_for_thumbnail(base_url, api_key, control_id)
        asset_ids.append(control_id)

    return asset_ids


def wait_for_wigglegram(
    base_url: str,
    api_key: str,
    *,
    album_name: str = "Wigglegrams",
    timeout_seconds: int = 180,
) -> bool:
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    deadline = time.time() + timeout_seconds
    print(f"Waiting up to {timeout_seconds}s for wigglegram in album '{album_name}' ...")

    while time.time() < deadline:
        albums = httpx.get(f"{base_url}/albums", headers=headers, timeout=30)
        if albums.is_success:
            target = next(
                (album for album in albums.json() if album.get("albumName") == album_name),
                None,
            )
            if target is not None:
                album = httpx.get(f"{base_url}/albums/{target['id']}", headers=headers, timeout=30)
                if album.is_success:
                    assets = album.json().get("assets", [])
                    gif_assets = [
                        asset
                        for asset in assets
                        if str(asset.get("originalFileName", "")).startswith("wiggle_")
                        or str(asset.get("originalMimeType", "")).endswith("gif")
                    ]
                    if gif_assets:
                        print(f"Wigglegram detected ({len(gif_assets)} GIF asset(s)).")
                        return True
        time.sleep(3)

    return False


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
    )
    parser.add_argument("--webhook-url", default=os.environ.get("WEBHOOK_URL", DEFAULT_WEBHOOK_URL))
    parser.add_argument(
        "--webhook-secret",
        default=os.environ.get("WEBHOOK_SECRET", DEFAULT_WEBHOOK_SECRET),
    )
    parser.add_argument("--upload-delay", type=float, default=1.0)
    parser.add_argument("--wait-timeout", type=int, default=180)
    parser.add_argument(
        "--skip-admin-setup",
        action="store_true",
        help="Reuse admin user; load API key from runtime env.",
    )
    args = parser.parse_args()

    wait_for_immich(args.immich_url)

    if args.skip_admin_setup:
        api_key = load_api_key_from_env(args.runtime_env)
        token = login(args.immich_url, args.admin_email, args.admin_password)
    else:
        ensure_admin(args.immich_url, args.admin_email, args.admin_password, args.admin_name)
        token = login(args.immich_url, args.admin_email, args.admin_password)
        api_key = create_api_key(args.immich_url, token, "image-helper-workflow-e2e")
        write_runtime_env(args.runtime_env, api_key, webhook_secret=args.webhook_secret)

    probe = probe_workflows(args.immich_url, access_token=token)
    if os.environ.get("WORKFLOW_DEBUG"):
        print(f"Workflow probe: {probe}")
    if not probe.available:
        print(
            "FAIL: Workflows API unavailable. "
            f"Set IMMICH_WORKFLOW_VERSION to a preview tag (e.g. next). Detail: {probe.error}",
            file=sys.stderr,
        )
        return 1

    method_info = discover_webhook_method(args.immich_url, access_token=token)
    if method_info is None:
        print("FAIL: Could not discover workflow webhook method on this Immich build.", file=sys.stderr)
        return 1

    if os.environ.get("WORKFLOW_DEBUG"):
        print(f"Using webhook method: {method_info}")

    workflow_id = ensure_wigglegram_workflow(
        args.immich_url,
        access_token=token,
        webhook_url=args.webhook_url,
        secret=args.webhook_secret,
        method_info=method_info,
    )
    print(f"Ensured workflow '{WIGGLEGRAM_WORKFLOW_NAME}' (id={workflow_id}).")

    upload_burst_fixtures_sequential(
        args.immich_url,
        api_key,
        args.fixtures_dir,
        delay_seconds=args.upload_delay,
    )

    if not wait_for_wigglegram(
        args.immich_url,
        api_key,
        timeout_seconds=args.wait_timeout,
    ):
        print("FAIL: Wigglegram was not created within the wait window.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
