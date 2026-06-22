from __future__ import annotations

import io
from typing import Literal

import imagehash
from PIL import Image, ImageOps

FrameFit = Literal["letterbox", "crop"]


def decode_image_rgb(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def oriented_size(image: Image.Image) -> tuple[int, int]:
    transposed = ImageOps.exif_transpose(image)
    return transposed.width, transposed.height


def compute_phash_from_bytes(data: bytes) -> str:
    rgb = decode_image_rgb(data)
    try:
        return str(imagehash.phash(rgb))
    finally:
        rgb.close()


def letterbox_frame(
    image: Image.Image,
    canvas_size: tuple[int, int],
    *,
    background: tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    canvas_w, canvas_h = canvas_size
    fitted = image.copy()
    fitted.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", canvas_size, background)
    offset_x = (canvas_w - fitted.width) // 2
    offset_y = (canvas_h - fitted.height) // 2
    canvas.paste(fitted, (offset_x, offset_y))
    fitted.close()
    return canvas


def center_crop_frame(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_w, target_h = target_size
    left = max((image.width - target_w) // 2, 0)
    top = max((image.height - target_h) // 2, 0)
    right = left + min(target_w, image.width)
    bottom = top + min(target_h, image.height)
    return image.crop((left, top, right, bottom))


def normalize_frames_for_gif(
    frames: list[Image.Image],
    *,
    max_size: int,
    frame_fit: FrameFit = "letterbox",
) -> list[Image.Image]:
    if not frames:
        return []

    if frame_fit == "letterbox":
        canvas_size = (max(frame.width for frame in frames), max(frame.height for frame in frames))
        normalized = [letterbox_frame(frame, canvas_size) for frame in frames]
    else:
        target_size = (min(frame.width for frame in frames), min(frame.height for frame in frames))
        normalized = [center_crop_frame(frame, target_size) for frame in frames]

    for frame in frames:
        frame.close()

    sized: list[Image.Image] = []
    for frame in normalized:
        copy = frame.copy()
        frame.close()
        copy.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        sized.append(copy)
    return sized
