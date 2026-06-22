from __future__ import annotations

from image_helper.immich_workflows import (
    _parse_webhook_method_payload,
    discover_webhook_method,
    probe_workflows,
)


def test_parse_plugin_methods_webhook_shape() -> None:
    payload = [
        {
            "key": "immich-plugin-core#httpWebhook",
            "name": "httpWebhook",
            "title": "HTTP webhook",
            "description": "POST asset payload to a URL",
            "types": ["AssetV1"],
            "schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "headers": {"type": "object"},
                },
            },
            "hostFunctions": False,
            "uiHints": [],
        }
    ]

    info = _parse_webhook_method_payload(payload)
    assert info is not None
    assert info.method == "immich-plugin-core#httpWebhook"
    assert info.url_key == "url"
    assert info.headers_key == "headers"


def test_parse_plugin_list_ignores_non_webhook_methods() -> None:
    payload = [
        {
            "id": "plugin-1",
            "name": "immich-plugin-core",
            "methods": [
                {
                    "key": "immich-plugin-core#assetArchive",
                    "name": "assetArchive",
                    "title": "Archive asset",
                    "description": "Archive",
                    "types": ["AssetV1"],
                    "schema": {"properties": {}},
                    "hostFunctions": False,
                    "uiHints": [],
                }
            ],
        }
    ]

    assert _parse_webhook_method_payload(payload) is None


def test_probe_workflows_available_without_webhook_step(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, payload: object | None = None) -> None:
            self.status_code = status_code
            self._payload = payload

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> object:
            return self._payload

        def raise_for_status(self) -> None:
            if not self.is_success:
                raise RuntimeError(self.status_code)

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            if path == "/server/version":
                return FakeResponse(200, {"major": 3, "minor": 0, "patch": 0})
            if path == "/workflows":
                return FakeResponse(200, [])
            if path == "/plugins/methods":
                return FakeResponse(200, [])
            return FakeResponse(404)

    monkeypatch.setattr("image_helper.immich_workflows.httpx.Client", FakeClient)

    result = probe_workflows("http://immich.test/api")
    assert result.available is True
    assert result.webhook_method is None
    assert result.error is None


def test_discover_webhook_method_skips_workflows_schema_route(monkeypatch) -> None:
    requested: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: object | None = None) -> None:
            self.status_code = status_code
            self._payload = payload

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> object:
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, path: str) -> FakeResponse:
            requested.append(path)
            if path == "/plugins/methods":
                return FakeResponse(200, [])
            if path == "/plugins":
                return FakeResponse(200, [])
            return FakeResponse(400)

    monkeypatch.setattr("image_helper.immich_workflows.httpx.Client", FakeClient)

    assert discover_webhook_method("http://immich.test/api") is None
    assert "/workflows/schema" not in requested
