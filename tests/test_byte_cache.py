from __future__ import annotations

from image_helper.byte_cache import ImageByteCache


def test_byte_cache_round_trip_memory() -> None:
    cache = ImageByteCache(max_entries=4, cache_dir=None)
    payload = b"image-bytes"
    cache.store("asset-1", "checksum-1", payload)
    assert cache.get("asset-1", "checksum-1") == payload
    assert cache.get("asset-1", "checksum-2") is None


def test_byte_cache_spills_to_disk(tmp_path) -> None:
    cache = ImageByteCache(max_entries=1, cache_dir=tmp_path / "bytes")
    cache.store("asset-1", "checksum-1", b"one")
    cache.store("asset-2", "checksum-2", b"two")
    assert cache.get("asset-2", "checksum-2") == b"two"
    assert cache.get("asset-1", "checksum-1") == b"one"
