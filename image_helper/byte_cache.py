from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path


class ImageByteCache:
    """LRU memory cache with optional on-disk spill for downloaded image bytes."""

    def __init__(
        self,
        *,
        max_entries: int = 64,
        cache_dir: Path | None = None,
        max_disk_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self._max_entries = max_entries
        self._cache_dir = cache_dir
        self._max_disk_bytes = max_disk_bytes
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._disk_bytes = 0

        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, asset_id: str, checksum: str | None) -> str:
        if checksum:
            return f"{asset_id}:{checksum}"
        return asset_id

    def _disk_path(self, cache_key: str) -> Path | None:
        if self._cache_dir is None:
            return None
        digest = hashlib.sha256(cache_key.encode()).hexdigest()
        return self._cache_dir / digest

    def get(self, asset_id: str, checksum: str | None) -> bytes | None:
        key = self._cache_key(asset_id, checksum)
        with self._lock:
            cached = self._memory.get(key)
            if cached is not None:
                self._memory.move_to_end(key)
                return cached

        disk_path = self._disk_path(key)
        if disk_path is not None and disk_path.is_file():
            data = disk_path.read_bytes()
            with self._lock:
                self._memory[key] = data
                self._memory.move_to_end(key)
                while len(self._memory) > self._max_entries:
                    self._memory.popitem(last=False)
            return data
        return None

    def store(self, asset_id: str, checksum: str | None, data: bytes) -> None:
        key = self._cache_key(asset_id, checksum)
        with self._lock:
            self._memory[key] = data
            self._memory.move_to_end(key)
            while len(self._memory) > self._max_entries:
                self._memory.popitem(last=False)

        disk_path = self._disk_path(key)
        if disk_path is None:
            return

        if disk_path.is_file():
            return

        disk_path.write_bytes(data)
        self._disk_bytes += len(data)
        self._prune_disk_if_needed()

    def _prune_disk_if_needed(self) -> None:
        if self._cache_dir is None or self._disk_bytes <= self._max_disk_bytes:
            return

        files = sorted(
            self._cache_dir.iterdir(),
            key=lambda path: path.stat().st_mtime,
        )
        for path in files:
            if self._disk_bytes <= self._max_disk_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            self._disk_bytes = max(0, self._disk_bytes - size)


def default_byte_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "image-helper" / "bytes"
    return Path(tempfile.gettempdir()) / "image-helper" / "bytes"
