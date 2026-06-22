from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from image_helper.cli import app
from image_helper.config import (
    Settings,
    SettingsLoadError,
    describe_settings,
    load_settings,
    redact_value,
    resolve_env_file,
)
from image_helper.doctor import run_doctor

runner = CliRunner()


def test_load_settings_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "IMMICH_URL=http://immich.test/api\nIMMICH_API_KEY=secret-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("IMMICH_URL", raising=False)
    monkeypatch.delenv("IMMICH_API_KEY", raising=False)

    settings = load_settings(env_file)
    assert settings.immich_url == "http://immich.test/api"
    assert settings.immich_api_key == "secret-key"


def test_env_file_overrides_shell_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "IMMICH_URL=http://from-file/api\nIMMICH_API_KEY=file-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("IMMICH_URL", "http://from-shell/api")
    monkeypatch.setenv("IMMICH_API_KEY", "shell-key")

    settings = load_settings(env_file)
    assert settings.immich_url == "http://from-shell/api"
    assert settings.immich_api_key == "shell-key"


def test_missing_api_key_raises_friendly_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IMMICH_API_KEY", raising=False)
    with pytest.raises(SettingsLoadError) as exc_info:
        load_settings(Path("/nonexistent/path.env"))
    assert "IMMICH_API_KEY" in str(exc_info.value)


def test_resolve_env_file_prefers_explicit_path(tmp_path: Path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("IMMICH_API_KEY=x\n", encoding="utf-8")
    assert resolve_env_file(env_file) == env_file


def test_describe_settings_redacts_secrets() -> None:
    settings = Settings(
        IMMICH_URL="http://localhost:2283/api",
        IMMICH_API_KEY="super-secret",
        WEBHOOK_SECRET="hook-secret",
    )
    rows = {row["name"]: row for row in describe_settings(settings)}
    assert rows["IMMICH_API_KEY"]["value"] == "********"
    assert rows["WEBHOOK_SECRET"]["value"] == "********"
    assert rows["IMMICH_URL"]["value"] == "http://localhost:2283/api"


def test_redact_value_leaves_non_secrets() -> None:
    assert redact_value("immich_url", "http://example.test") == "http://example.test"


def test_config_show_redacts_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "IMMICH_URL=http://immich.test/api\nIMMICH_API_KEY=hidden-key\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["--env-file", str(env_file), "config", "show"])
    assert result.exit_code == 0
    assert "hidden-key" not in result.stdout
    assert "********" in result.stdout


def test_doctor_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server/ping"):
            return httpx.Response(200, json={"res": "pong"})
        if request.url.path.endswith("/users/me"):
            return httpx.Response(
                200,
                json={
                    "permissions": [
                        "asset.read",
                        "asset.view",
                        "asset.download",
                        "asset.upload",
                        "album.read",
                        "album.create",
                        "albumAsset.create",
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr("image_helper.doctor.httpx.Client", client_factory)

    result = run_doctor("http://immich.test/api", "test-key")
    assert result["ping_ok"] is True
    assert result["auth_ok"] is True
    assert result["permissions_ok"] is True


def test_doctor_reports_missing_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server/ping"):
            return httpx.Response(200, json={"res": "pong"})
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"permissions": ["asset.read"]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr("image_helper.doctor.httpx.Client", client_factory)

    result = run_doctor("http://immich.test/api", "test-key")
    assert result["auth_ok"] is True
    assert result["permissions_ok"] is False
    assert "asset.upload" in result["missing_permissions"]
