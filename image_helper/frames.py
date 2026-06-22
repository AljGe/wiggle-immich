from __future__ import annotations

import io
from typing import Literal

import imagehash
from PIL import Image, ImageOps

FrameFit = Literal["letterbox", "crop"]
OutputFormat = Literal["gif", "webp"]
GifDither = bool


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


def normalize_frames(
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


def normalize_frames_for_gif(
    frames: list[Image.Image],
    *,
    max_size: int,
    frame_fit: FrameFit = "letterbox",
) -> list[Image.Image]:
    return normalize_frames(frames, max_size=max_size, frame_fit=frame_fit)


def apply_boomerang(frames: list[Image.Image], *, enabled: bool) -> list[Image.Image]:
    if not enabled or len(frames) <= 1:
        return frames
    return frames + list(reversed(frames))[1:]


def _sample_palette_pixels(frames: list[Image.Image], *, max_samples: int = 65536) -> Image.Image:
    if not frames:
        raise ValueError("frames must not be empty")

    per_frame = max(1, max_samples // len(frames))
    strips: list[Image.Image] = []
    for frame in frames:
        sample = frame.copy()
        sample.thumbnail((256, 256), Image.Resampling.LANCZOS)
        strips.append(sample)
    width = sum(image.width for image in strips)
    height = max(image.height for image in strips)
    canvas = Image.new("RGB", (width, height))
    offset = 0
    for image in strips:
        canvas.paste(image, (offset, 0))
        offset += image.width
        image.close()
    return canvas


def quantize_frames_to_palette(
    frames: list[Image.Image],
    *,
    dither: bool = True,
    colors: int = 256,
) -> list[Image.Image]:
    if not frames:
        return []

    palette_source = _sample_palette_pixels(frames)
    try:
        palette_image = palette_source.quantize(
            colors=colors,
            method=Image.Quantize.MEDIANCUT,
        )
        quantized: list[Image.Image] = []
        for frame in frames:
            converted = frame.convert("RGB")
            quantized.append(
                converted.quantize(
                    palette=palette_image,
                    dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE,
                )
            )
            converted.close()
        return quantized
    finally:
        palette_source.close()


def encode_gif(
    frames: list[Image.Image],
    *,
    frame_duration_ms: int,
    dither: bool = True,
) -> bytes:
    if not frames:
        raise ValueError("frames must not be empty")

    palette_frames = quantize_frames_to_palette(frames, dither=dither)
    buffer = io.BytesIO()
    try:
        palette_frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=palette_frames[1:],
            duration=frame_duration_ms,
            loop=0,
            optimize=True,
            disposal=2,
        )
        return buffer.getvalue()
    finally:
        for frame in palette_frames:
            frame.close()


def encode_webp(
    frames: list[Image.Image],
    *,
    frame_duration_ms: int,
    quality: int = 85,
    lossless: bool = False,
    method: int = 4,
) -> bytes:
    if not frames:
        raise ValueError("frames must not be empty")

    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
        quality=quality,
        lossless=lossless,
        method=method,
    )
    return buffer.getvalue()
