from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    immich_url: str = Field(default="http://localhost:2283/api", alias="IMMICH_URL")
    immich_api_key: str = Field(alias="IMMICH_API_KEY")

    hash_db_path: Path = Field(default=Path("./data/hashes.sqlite3"), alias="HASH_DB_PATH")

    wiggle_threshold: int = Field(default=10, alias="WIGGLE_THRESHOLD")
    wiggle_time_window_seconds: float = Field(default=3.0, alias="WIGGLE_TIME_WINDOW_SECONDS")
    wiggle_frame_duration_ms: int = Field(default=100, alias="WIGGLE_FRAME_DURATION_MS")
    wiggle_max_size: int = Field(default=600, alias="WIGGLE_MAX_SIZE")
    wiggle_boomerang: bool = Field(default=True, alias="WIGGLE_BOOMERANG")
    wiggle_album_name: str = Field(default="Wigglegrams", alias="WIGGLE_ALBUM_NAME")

    daemon_poll_interval_seconds: int = Field(default=60, alias="DAEMON_POLL_INTERVAL_SECONDS")

    webhook_host: str = Field(default="0.0.0.0", alias="WEBHOOK_HOST")
    webhook_port: int = Field(default=8765, alias="WEBHOOK_PORT")
    webhook_secret: str | None = Field(default=None, alias="WEBHOOK_SECRET")

    device_id: str = Field(default="image-helper", alias="DEVICE_ID")

    @property
    def immich_base_url(self) -> str:
        return self.immich_url.rstrip("/")
