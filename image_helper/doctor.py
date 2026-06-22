from __future__ import annotations

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


def run_doctor(base_url: str, api_key: str) -> dict[str, object]:
    base_url = base_url.rstrip("/")
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
    }
    result: dict[str, object] = {
        "ping_ok": False,
        "auth_ok": False,
        "permissions_ok": False,
        "missing_permissions": [],
        "error": None,
    }

    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=15.0) as client:
            ping = client.get("/server/ping")
            result["ping_ok"] = ping.status_code == 200
            if not result["ping_ok"]:
                result["error"] = f"Ping failed ({ping.status_code})"
                return result

            me = client.get("/users/me")
            if me.is_error:
                result["error"] = f"Auth failed ({me.status_code}): {me.text[:200]}"
                return result

            result["auth_ok"] = True
            payload = me.json()
            permissions = set(payload.get("permissions") or [])
            missing = [perm for perm in REQUIRED_PERMISSIONS if perm not in permissions]
            result["missing_permissions"] = missing
            result["permissions_ok"] = not missing
            if missing:
                result["error"] = "API key is missing required permissions"
            return result
    except httpx.HTTPError as exc:
        result["error"] = str(exc)
        return result
