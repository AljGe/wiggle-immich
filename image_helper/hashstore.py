from __future__ import annotations

import io
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from image_helper.frames import oriented_size
from image_helper.models import AssetRecord

_ASSET_COLUMNS = (
    "asset_id",
    "phash",
    "local_datetime",
    "checksum",
    "width",
    "height",
    "original_file_name",
    "stack_id",
    "is_primary_in_stack",
    "indexed_at",
)


class HashStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(asset_hashes)").fetchall()
        }
        migrations = [
            ("width", "INTEGER"),
            ("height", "INTEGER"),
            ("original_file_name", "TEXT"),
            ("stack_id", "TEXT"),
            ("is_primary_in_stack", "INTEGER"),
        ]
        for column_name, column_type in migrations:
            if column_name not in existing:
                conn.execute(
                    f"ALTER TABLE asset_hashes ADD COLUMN {column_name} {column_type}"
                )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS asset_hashes (
                  asset_id TEXT PRIMARY KEY,
                  phash TEXT NOT NULL,
                  local_datetime TEXT NOT NULL,
                  checksum TEXT,
                  indexed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_asset_hashes_local_datetime
                  ON asset_hashes(local_datetime);

                CREATE TABLE IF NOT EXISTS wiggle_exports (
                  group_key TEXT PRIMARY KEY,
                  gif_asset_id TEXT NOT NULL,
                  exported_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daemon_state (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )
            self._migrate_schema(conn)
            conn.commit()

    @contextmanager
    def _transaction(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, record: AssetRecord) -> None:
        self.upsert_many([record])

    def upsert_many(self, records: list[AssetRecord]) -> None:
        if not records:
            return

        now = _utc_now_iso()
        rows = [_record_to_row(record, now) for record in records]
        with self._transaction() as conn:
            conn.executemany(
                """
                INSERT INTO asset_hashes (
                  asset_id,
                  phash,
                  local_datetime,
                  checksum,
                  width,
                  height,
                  original_file_name,
                  stack_id,
                  is_primary_in_stack,
                  indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                  phash = excluded.phash,
                  local_datetime = excluded.local_datetime,
                  checksum = excluded.checksum,
                  width = excluded.width,
                  height = excluded.height,
                  original_file_name = excluded.original_file_name,
                  stack_id = excluded.stack_id,
                  is_primary_in_stack = excluded.is_primary_in_stack,
                  indexed_at = excluded.indexed_at
                """,
                rows,
            )

    def get(self, asset_id: str) -> AssetRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_ASSET_COLUMNS)} FROM asset_hashes WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def has_checksum(self, checksum: str | None) -> bool:
        if not checksum:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM asset_hashes WHERE checksum = ? LIMIT 1",
                (checksum,),
            ).fetchone()
        return row is not None

    def list_all(self) -> list[AssetRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_ASSET_COLUMNS)} FROM asset_hashes ORDER BY local_datetime ASC"
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_in_range(
        self,
        start: datetime,
        end: datetime,
    ) -> list[AssetRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_ASSET_COLUMNS)}
                FROM asset_hashes
                WHERE local_datetime >= ? AND local_datetime <= ?
                ORDER BY local_datetime ASC
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM asset_hashes").fetchone()
        return int(row["count"])

    def mark_exported(self, group_key: str, gif_asset_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO wiggle_exports (group_key, gif_asset_id, exported_at)
                VALUES (?, ?, ?)
                ON CONFLICT(group_key) DO UPDATE SET
                  gif_asset_id = excluded.gif_asset_id,
                  exported_at = excluded.exported_at
                """,
                (group_key, gif_asset_id, _utc_now_iso()),
            )

    def is_exported(self, group_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM wiggle_exports WHERE group_key = ? LIMIT 1",
                (group_key,),
            ).fetchone()
        return row is not None

    def get_exported_asset_id(self, group_key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT gif_asset_id FROM wiggle_exports WHERE group_key = ?",
                (group_key,),
            ).fetchone()
        if row is None:
            return None
        return row["gif_asset_id"]

    def get_daemon_cursor(self) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM daemon_state WHERE key = 'last_poll_at'"
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["value"])

    def set_daemon_cursor(self, value: datetime) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO daemon_state (key, value) VALUES ('last_poll_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (value.isoformat(),),
            )


def dimensions_from_image_bytes(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return oriented_size(image)


def _record_to_row(record: AssetRecord, indexed_at: str) -> tuple:
    primary = (
        int(record.is_primary_in_stack)
        if record.is_primary_in_stack is not None
        else None
    )
    return (
        record.asset_id,
        record.phash,
        record.local_datetime.isoformat(),
        record.checksum,
        record.width,
        record.height,
        record.original_file_name,
        record.stack_id,
        primary,
        indexed_at,
    )


def _row_to_record(row: sqlite3.Row) -> AssetRecord:
    primary_value = row["is_primary_in_stack"]
    is_primary = None if primary_value is None else bool(primary_value)
    return AssetRecord(
        asset_id=row["asset_id"],
        phash=row["phash"],
        local_datetime=datetime.fromisoformat(row["local_datetime"]),
        checksum=row["checksum"],
        width=row["width"],
        height=row["height"],
        original_file_name=row["original_file_name"],
        stack_id=row["stack_id"],
        is_primary_in_stack=is_primary,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
