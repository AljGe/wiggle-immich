from __future__ import annotations

import io
from datetime import datetime

import pytest
from PIL import Image, ImageDraw

from image_helper.frames import (
    compute_phash_from_bytes,
    decode_image_rgb,
    letterbox_frame,
    normalize_frames_for_gif,
    oriented_size,
)



def test_decode_image_rgb_applies_exif_orientation() -> None:
    image = Image.new("RGB", (120, 80), "red")
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())

    rgb = decode_image_rgb(buffer.getvalue())
    try:
        assert rgb.size == (80, 120)
    finally:
        rgb.close()


def test_oriented_size_matches_transposed_dimensions() -> None:
    image = Image.new("RGB", (200, 100), "blue")
    exif = image.getexif()
    exif[274] = 8
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())

    with Image.open(io.BytesIO(buffer.getvalue())) as loaded:
        assert oriented_size(loaded) == (100, 200)


def test_letterbox_frame_preserves_aspect_ratio_on_shared_canvas() -> None:
    wide = Image.new("RGB", (200, 100), "green")
    tall = Image.new("RGB", (100, 200), "yellow")
    canvas_size = (200, 200)

    wide_box = letterbox_frame(wide, canvas_size)
    tall_box = letterbox_frame(tall, canvas_size)
    try:
        assert wide_box.size == canvas_size
        assert tall_box.size == canvas_size
    finally:
        wide.close()
        tall.close()
        wide_box.close()
        tall_box.close()


def test_normalize_frames_for_gif_uses_common_canvas() -> None:
    frames = [
        Image.new("RGB", (200, 100), "red"),
        Image.new("RGB", (180, 120), "blue"),
    ]
    normalized = normalize_frames_for_gif(frames, max_size=300, frame_fit="letterbox")
    try:
        assert len(normalized) == 2
        assert normalized[0].size == normalized[1].size
    finally:
        for frame in normalized:
            frame.close()


def test_compute_phash_differs_for_different_images() -> None:
    left_image = Image.new("RGB", (120, 80), "white")
    left_draw = ImageDraw.Draw(left_image)
    left_draw.rectangle([10, 10, 110, 70], fill="red")
    right_image = Image.new("RGB", (120, 80), "white")
    right_draw = ImageDraw.Draw(right_image)
    right_draw.ellipse([10, 10, 110, 70], fill="blue")

    left_buffer = io.BytesIO()
    right_buffer = io.BytesIO()
    left_image.save(left_buffer, format="JPEG")
    right_image.save(right_buffer, format="JPEG")

    left = compute_phash_from_bytes(left_buffer.getvalue())
    right = compute_phash_from_bytes(right_buffer.getvalue())
    assert left != right
