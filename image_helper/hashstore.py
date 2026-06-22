from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from image_helper.models import AssetRecord


class HashStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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
        rows = [
            (
                record.asset_id,
                record.phash,
                record.local_datetime.isoformat(),
                record.checksum,
                now,
            )
            for record in records
        ]
        with self._transaction() as conn:
            conn.executemany(
                """
                INSERT INTO asset_hashes (asset_id, phash, local_datetime, checksum, indexed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                  phash = excluded.phash,
                  local_datetime = excluded.local_datetime,
                  checksum = excluded.checksum,
                  indexed_at = excluded.indexed_at
                """,
                rows,
            )

    def get(self, asset_id: str) -> AssetRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT asset_id, phash, local_datetime, checksum FROM asset_hashes WHERE asset_id = ?",
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
                "SELECT asset_id, phash, local_datetime, checksum FROM asset_hashes ORDER BY local_datetime ASC"
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


def _row_to_record(row: sqlite3.Row) -> AssetRecord:
    return AssetRecord(
        asset_id=row["asset_id"],
        phash=row["phash"],
        local_datetime=datetime.fromisoformat(row["local_datetime"]),
        checksum=row["checksum"],
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
