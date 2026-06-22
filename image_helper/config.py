from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_FIELDS = frozenset({"immich_api_key", "webhook_secret"})
SettingSource = Literal["default", "env", "file"]


class SettingsLoadError(Exception):
    """Raised when settings cannot be loaded from the environment."""

    def __init__(self, error: ValidationError) -> None:
        self.error = error
        missing = [
            err["loc"][0]
            for err in error.errors()
            if err["type"] == "missing"
        ]
        self.missing_fields = missing
        super().__init__(format_settings_error(error))


def xdg_config_env_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "image-helper" / "env"
    return Path.home() / ".config" / "image-helper" / "env"


def default_env_file_path() -> Path:
    return Path(".env")


def resolve_env_file(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        path = explicit.expanduser()
        return path if path.is_file() else path

    env_var = os.environ.get("IMAGE_HELPER_ENV_FILE")
    if env_var:
        path = Path(env_var).expanduser()
        if path.is_file():
            return path

    cwd_env = Path(".env")
    if cwd_env.is_file():
        return cwd_env

    xdg_env = xdg_config_env_path()
    if xdg_env.is_file():
        return xdg_env

    return None


def discover_env_file_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_var = os.environ.get("IMAGE_HELPER_ENV_FILE")
    if env_var:
        candidates.append(Path(env_var).expanduser())
    candidates.append(Path(".env"))
    candidates.append(xdg_config_env_path())
    return candidates


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def format_settings_error(error: ValidationError) -> str:
    lines = ["Configuration error:"]
    for err in error.errors():
        field = ".".join(str(part) for part in err["loc"])
        if err["type"] == "missing":
            lines.append(f"  - {field} is required")
        else:
            lines.append(f"  - {field}: {err['msg']}")

    lines.extend(
        [
            "",
            "Fix:",
            "  image-helper config init",
            "  # or copy .env.example to .env and edit IMMICH_URL / IMMICH_API_KEY",
        ]
    )
    return "\n".join(lines)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    immich_url: str = Field(default="http://localhost:2283/api", alias="IMMICH_URL")
    immich_api_key: str = Field(alias="IMMICH_API_KEY")

    hash_db_path: Path = Field(default=Path("./data/hashes.sqlite3"), alias="HASH_DB_PATH")

    wiggle_threshold: int = Field(default=10, alias="WIGGLE_THRESHOLD")
    wiggle_min_distance: int = Field(default=2, alias="WIGGLE_MIN_DISTANCE")
    wiggle_time_window_seconds: float = Field(default=3.0, alias="WIGGLE_TIME_WINDOW_SECONDS")
    wiggle_frame_duration_ms: int = Field(default=100, alias="WIGGLE_FRAME_DURATION_MS")
    wiggle_max_size: int = Field(default=600, alias="WIGGLE_MAX_SIZE")
    wiggle_boomerang: bool = Field(default=True, alias="WIGGLE_BOOMERANG")
    wiggle_album_name: str = Field(default="Wigglegrams", alias="WIGGLE_ALBUM_NAME")
    wiggle_hash_source: Literal["original", "thumbnail"] = Field(
        default="original",
        alias="WIGGLE_HASH_SOURCE",
    )
    wiggle_frame_fit: Literal["letterbox", "crop"] = Field(
        default="letterbox",
        alias="WIGGLE_FRAME_FIT",
    )
    wiggle_max_dimension_drift: float = Field(
        default=0.02,
        alias="WIGGLE_MAX_DIMENSION_DRIFT",
    )
    wiggle_exclude_stacked: bool = Field(default=True, alias="WIGGLE_EXCLUDE_STACKED")
    wiggle_min_frames: int = Field(default=2, alias="WIGGLE_MIN_FRAMES")
    wiggle_neighbor_search_primary_only: bool = Field(
        default=False,
        alias="WIGGLE_NEIGHBOR_SEARCH_PRIMARY_ONLY",
    )
    wiggle_max_gap_frames: int = Field(default=0, alias="WIGGLE_MAX_GAP_FRAMES")
    wiggle_settle_seconds: float = Field(default=0.0, alias="WIGGLE_SETTLE_SECONDS")
    wiggle_require_burst_metadata: bool = Field(
        default=False,
        alias="WIGGLE_REQUIRE_BURST_METADATA",
    )
    wiggle_stack_with_sources: bool = Field(
        default=False,
        alias="WIGGLE_STACK_WITH_SOURCES",
    )
    index_workers: int = Field(default=4, alias="INDEX_WORKERS")

    daemon_poll_interval_seconds: int = Field(default=60, alias="DAEMON_POLL_INTERVAL_SECONDS")

    webhook_host: str = Field(default="0.0.0.0", alias="WEBHOOK_HOST")
    webhook_port: int = Field(default=8765, alias="WEBHOOK_PORT")
    webhook_secret: str | None = Field(default=None, alias="WEBHOOK_SECRET")

    device_id: str = Field(default="image-helper", alias="DEVICE_ID")

    @property
    def immich_base_url(self) -> str:
        return self.immich_url.rstrip("/")


def load_settings(env_file: Path | None = None) -> Settings:
    resolved = resolve_env_file(env_file)
    try:
        if resolved is not None and resolved.is_file():
            return Settings(_env_file=resolved)
        return Settings()
    except ValidationError as exc:
        raise SettingsLoadError(exc) from exc


def redact_value(field_name: str, value: Any) -> str:
    if field_name in SECRET_FIELDS and value:
        return "********"
    if value is None:
        return ""
    return str(value)


def describe_settings(
    settings: Settings | None = None,
    *,
    env_file: Path | None = None,
) -> list[dict[str, str]]:
    resolved = resolve_env_file(env_file)
    file_values = parse_env_file(resolved) if resolved and resolved.is_file() else {}
    if settings is None:
        settings = load_settings(env_file)

    rows: list[dict[str, str]] = []
    for field_name, field_info in Settings.model_fields.items():
        alias = field_info.alias or field_name.upper()
        value = getattr(settings, field_name)
        if alias in os.environ:
            source: SettingSource = "env"
        elif alias in file_values:
            source = "file"
        else:
            source = "default"

        rows.append(
            {
                "name": alias,
                "value": redact_value(field_name, value),
                "source": source,
            }
        )
    return rows


def find_env_example() -> Path | None:
    candidates = [
        Path(".env.example"),
        Path(__file__).resolve().parent.parent / ".env.example",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
