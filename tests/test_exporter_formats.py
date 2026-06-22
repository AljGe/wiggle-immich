from __future__ import annotations

import io
from datetime import datetime, timezone

from PIL import Image

from image_helper.exporter import ExportOptions, encode_wigglegram
from image_helper.frames import apply_boomerang, encode_gif, encode_webp


def _solid_frames(count: int = 3, *, size: tuple[int, int] = (120, 80)) -> list[Image.Image]:
    colors = ["#e74c3c", "#3498db", "#2ecc71"]
    return [
        Image.new("RGB", size, colors[index % len(colors)])
        for index in range(count)
    ]


def test_encode_gif_produces_gif_bytes() -> None:
    frames = apply_boomerang(_solid_frames(), enabled=True)
    try:
        data = encode_gif(frames, frame_duration_ms=100, dither=True)
        assert data.startswith(b"GIF")
    finally:
        for frame in frames:
            frame.close()


def test_encode_webp_produces_webp_bytes() -> None:
    frames = apply_boomerang(_solid_frames(), enabled=False)
    try:
        data = encode_webp(frames, frame_duration_ms=100, quality=85, lossless=False)
        assert data.startswith(b"RIFF")
        assert b"WEBP" in data[:16]
    finally:
        for frame in frames:
            frame.close()


def test_encode_wigglegram_webp_smaller_than_gif() -> None:
    frames = _solid_frames(count=4, size=(320, 240))
    options = ExportOptions(output_format="webp", frame_duration_ms=80)
    try:
        webp_bytes = encode_wigglegram(frames, options=options)
        gif_options = ExportOptions(output_format="gif", frame_duration_ms=80)
        frames_for_gif = _solid_frames(count=4, size=(320, 240))
        gif_bytes = encode_wigglegram(frames_for_gif, options=gif_options)
        assert len(webp_bytes) < len(gif_bytes)
    finally:
        for frame in frames:
            frame.close()


def test_build_export_filename_uses_extension() -> None:
    from image_helper.exporter import build_export_filename
    from image_helper.models import AssetRecord, WiggleGroup

    group = WiggleGroup(
        assets=(
            AssetRecord(
                asset_id="a",
                local_datetime=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                phash="0" * 16,
            ),
        ),
        distances=(),
    )
    assert build_export_filename(group, output_format="webp").endswith(".webp")
    assert build_export_filename(group, output_format="gif").endswith(".gif")
