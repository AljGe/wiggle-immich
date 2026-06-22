from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WIGGLEGRAM_WORKFLOW_NAME = "image-helper wigglegram"
WEBHOOK_HINTS = ("webhook", "http", "post", "request", "callout")
URL_CONFIG_KEYS = ("url", "endpoint", "webhookUrl", "webhook_url", "targetUrl", "target_url")
SECRET_HEADER_KEYS = ("x-immich-webhook-secret", "X-Immich-Webhook-Secret")


@dataclass(frozen=True)
class WebhookMethodInfo:
    method: str
    url_key: str
    headers_key: str | None
    config_template: dict[str, Any]


@dataclass(frozen=True)
class WorkflowProbeResult:
    available: bool
    version: str | None
    webhook_method: str | None
    error: str | None = None


def _auth_headers(*, api_key: str | None = None, access_token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def probe_workflows(
    base_url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
) -> WorkflowProbeResult:
    base_url = base_url.rstrip("/")
    headers = _auth_headers(api_key=api_key, access_token=access_token)
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=15.0) as client:
            version = None
            version_resp = client.get("/server/version")
            if version_resp.is_success:
                v = version_resp.json()
                if isinstance(v, dict) and "major" in v:
                    version = f"{v.get('major', '?')}.{v.get('minor', '?')}.{v.get('patch', '?')}"
            workflows_resp = client.get("/workflows")
            if workflows_resp.status_code == 404:
                return WorkflowProbeResult(
                    available=False,
                    version=version,
                    webhook_method=None,
                    error="Workflows API not found (Immich preview required)",
                )
            workflows_resp.raise_for_status()
            webhook = discover_webhook_method(base_url, api_key=api_key, access_token=access_token)
            return WorkflowProbeResult(
                available=True,
                version=version,
                webhook_method=webhook.method if webhook else None,
            )
    except httpx.HTTPError as exc:
        return WorkflowProbeResult(
            available=False,
            version=None,
            webhook_method=None,
            error=str(exc),
        )


def _iter_method_entries(payload: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and "method" in item:
                entries.append(item)
            elif isinstance(item, dict):
                entries.extend(_iter_method_entries(item.get("methods", [])))
    elif isinstance(payload, dict):
        if "methods" in payload and isinstance(payload["methods"], list):
            entries.extend(_iter_method_entries(payload["methods"]))
        for value in payload.values():
            if isinstance(value, list):
                entries.extend(_iter_method_entries(value))
    return entries


def _method_name(entry: dict[str, Any]) -> str:
    method = entry.get("method") or entry.get("name") or ""
    plugin = entry.get("plugin") or entry.get("pluginName")
    if plugin and "#" not in method:
        return f"{plugin}#{method}"
    return str(method)


def _looks_like_webhook_method(entry: dict[str, Any]) -> bool:
    haystack = json.dumps(entry).lower()
    return any(hint in haystack for hint in WEBHOOK_HINTS)


def _pick_url_key(config_schema: dict[str, Any]) -> str | None:
    properties = config_schema.get("properties") if isinstance(config_schema, dict) else None
    if isinstance(properties, dict):
        for key in properties:
            if key in URL_CONFIG_KEYS or "url" in key.lower():
                return key
    for key in URL_CONFIG_KEYS:
        if key in config_schema:
            return key
    return None


def _pick_headers_key(config_schema: dict[str, Any]) -> str | None:
    properties = config_schema.get("properties") if isinstance(config_schema, dict) else None
    if isinstance(properties, dict):
        for key in properties:
            if "header" in key.lower():
                return key
    if "headers" in config_schema:
        return "headers"
    return None


def discover_webhook_method(
    base_url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
) -> WebhookMethodInfo | None:
    base_url = base_url.rstrip("/")
    headers = _auth_headers(api_key=api_key, access_token=access_token)
    candidate_paths = (
        "/workflows/schema",
        "/plugins",
        "/workflow-plugins",
    )

    with httpx.Client(base_url=base_url, headers=headers, timeout=15.0) as client:
        for path in candidate_paths:
            response = client.get(path)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            info = _parse_webhook_method_payload(response.json())
            if info is not None:
                logger.info("Discovered webhook method %s from %s", info.method, path)
                return info

    return _fallback_webhook_method()


def _parse_webhook_method_payload(payload: Any) -> WebhookMethodInfo | None:
    for entry in _iter_method_entries(payload):
        if not _looks_like_webhook_method(entry):
            continue
        method = _method_name(entry)
        config_schema = entry.get("configSchema") or entry.get("config") or {}
        if not isinstance(config_schema, dict):
            config_schema = {}
        url_key = _pick_url_key(config_schema) or "url"
        headers_key = _pick_headers_key(config_schema)
        config_template: dict[str, Any] = {url_key: ""}
        if headers_key:
            config_template[headers_key] = {}
        return WebhookMethodInfo(
            method=method,
            url_key=url_key,
            headers_key=headers_key,
            config_template=config_template,
        )
    return None


def _fallback_webhook_method() -> WebhookMethodInfo:
  # Common preview naming; bootstrap logs when this fallback is used.
    return WebhookMethodInfo(
        method="immich-plugin-core#httpWebhook",
        url_key="url",
        headers_key="headers",
        config_template={"url": "", "headers": {}},
    )


def build_wigglegram_workflow(
    webhook_url: str,
    *,
    secret: str | None = None,
    method_info: WebhookMethodInfo | None = None,
) -> dict[str, Any]:
    info = method_info or _fallback_webhook_method()
    config = dict(info.config_template)
    config[info.url_key] = webhook_url
    if secret and info.headers_key:
        config[info.headers_key] = {SECRET_HEADER_KEYS[0]: secret}

    return {
        "name": WIGGLEGRAM_WORKFLOW_NAME,
        "description": "Trigger image-helper wigglegram export on new assets",
        "enabled": True,
        "trigger": "AssetCreate",
        "steps": [
            {
                "method": info.method,
                "config": config,
                "enabled": True,
            }
        ],
    }


def list_workflows(
    base_url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
) -> list[dict[str, Any]]:
    headers = _auth_headers(api_key=api_key, access_token=access_token)
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=30.0) as client:
        response = client.get("/workflows")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return payload.get("items", [])


def find_wigglegram_workflow(workflows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for workflow in workflows:
        if workflow.get("name") == WIGGLEGRAM_WORKFLOW_NAME:
            return workflow
    return None


def ensure_wigglegram_workflow(
    base_url: str,
    *,
    api_key: str | None = None,
    access_token: str | None = None,
    webhook_url: str,
    secret: str | None = None,
    method_info: WebhookMethodInfo | None = None,
) -> str:
    base_url = base_url.rstrip("/")
    headers = _auth_headers(api_key=api_key, access_token=access_token)
    body = build_wigglegram_workflow(webhook_url, secret=secret, method_info=method_info)

    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
        existing = find_wigglegram_workflow(
            list_workflows(base_url, api_key=api_key, access_token=access_token)
        )
        if existing:
            workflow_id = existing["id"]
            response = client.put(f"/workflows/{workflow_id}", json=body)
            response.raise_for_status()
            return workflow_id

        response = client.post("/workflows", json=body)
        response.raise_for_status()
        return response.json()["id"]


def load_workflow_template(path: Path | None = None) -> dict[str, Any]:
    template_path = path or Path(__file__).resolve().parent.parent / "testdata/workflows/wigglegram-webhook.json"
    return json.loads(template_path.read_text(encoding="utf-8"))


def substitute_workflow_template(
    template: dict[str, Any],
  *,
    webhook_url: str,
    secret: str | None,
    method_info: WebhookMethodInfo,
) -> dict[str, Any]:
    raw = json.dumps(template)
    raw = raw.replace("${WEBHOOK_URL}", webhook_url)
    raw = raw.replace("${WEBHOOK_SECRET}", secret or "")
    raw = raw.replace("${WEBHOOK_METHOD}", method_info.method)
    body = json.loads(raw)
    if body.get("steps"):
        step = body["steps"][0]
        step["method"] = method_info.method
        config = dict(method_info.config_template)
        config[method_info.url_key] = webhook_url
        if secret and method_info.headers_key:
            config[method_info.headers_key] = {SECRET_HEADER_KEYS[0]: secret}
        step["config"] = config
    return body
