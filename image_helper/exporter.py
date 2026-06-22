from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from PIL import Image

from image_helper.byte_cache import ImageByteCache
from image_helper.frames import (
    FrameFit,
    OutputFormat,
    apply_boomerang,
    decode_image_rgb,
    encode_gif,
    encode_webp,
    normalize_frames,
)
from image_helper.immich import ImmichClient
from image_helper.models import WiggleGroup
from image_helper.stabilize import StabilizeMode, StabilizeReference, stabilize_frames


@dataclass(frozen=True)
class StabilizeOptions:
    enabled: bool = True
    mode: StabilizeMode = "auto"
    reference: StabilizeReference = "middle"
    crop_to_overlap: bool = True
    max_rotation_deg: float = 3.0
    working_max_edge: int = 1024


@dataclass(frozen=True)
class ExportOptions:
    frame_duration_ms: int = 100
    max_size: int = 900
    boomerang: bool = True
    frame_fit: FrameFit = "letterbox"
    stabilize: StabilizeOptions | None = None
    output_format: OutputFormat = "webp"
    webp_quality: int = 85
    webp_lossless: bool = False
    gif_dither: bool = True
    download_workers: int = 4


@dataclass(frozen=True)
class WigglegramArtifact:
    data: bytes
    filename: str
    mime_type: str
    output_format: OutputFormat


def _download_original_bytes(
    client: ImmichClient,
    asset_id: str,
    *,
    checksum: str | None,
    byte_cache: ImageByteCache | None,
) -> bytes:
    if byte_cache is not None:
        cached = byte_cache.get(asset_id, checksum)
        if cached is not None:
            return cached
    data = client.download_original(asset_id)
    if byte_cache is not None:
        byte_cache.store(asset_id, checksum, data)
    return data


def _download_group_frames(
    client: ImmichClient,
    group: WiggleGroup,
    *,
    download_workers: int,
    byte_cache: ImageByteCache | None,
) -> list[Image.Image]:
    if download_workers <= 1 or len(group.assets) <= 1:
        frames: list[Image.Image] = []
        for asset in group.assets:
            data = _download_original_bytes(
                client,
                asset.asset_id,
                checksum=asset.checksum,
                byte_cache=byte_cache,
            )
            frames.append(decode_image_rgb(data))
        return frames

    frames_by_index: dict[int, Image.Image] = {}
    with ThreadPoolExecutor(max_workers=download_workers) as executor:
        futures = {
            executor.submit(
                _download_original_bytes,
                client,
                asset.asset_id,
                checksum=asset.checksum,
                byte_cache=byte_cache,
            ): index
            for index, asset in enumerate(group.assets)
        }
        for future in as_completed(futures):
            index = futures[future]
            frames_by_index[index] = decode_image_rgb(future.result())

    return [frames_by_index[index] for index in range(len(group.assets))]


def build_wigglegram_frames(
    client: ImmichClient,
    group: WiggleGroup,
    *,
    options: ExportOptions,
    byte_cache: ImageByteCache | None = None,
) -> list[Image.Image]:
    stabilize = options.stabilize or StabilizeOptions()
    raw_frames = _download_group_frames(
        client,
        group,
        download_workers=options.download_workers,
        byte_cache=byte_cache,
    )

    try:
        if stabilize.enabled and stabilize.mode != "off":
            working_frames = stabilize_frames(
                raw_frames,
                mode=stabilize.mode,
                reference=stabilize.reference,
                crop_to_overlap=stabilize.crop_to_overlap,
                max_rotation_deg=stabilize.max_rotation_deg,
                working_max_edge=stabilize.working_max_edge,
            )
            for frame in raw_frames:
                frame.close()
            raw_frames = working_frames

        frames = normalize_frames(
            raw_frames,
            max_size=options.max_size,
            frame_fit=options.frame_fit,
        )
        return apply_boomerang(frames, enabled=options.boomerang)
    except Exception:
        for frame in raw_frames:
            frame.close()
        raise


def encode_wigglegram(
    frames: list[Image.Image],
    *,
    options: ExportOptions,
) -> bytes:
    try:
        if options.output_format == "webp":
            return encode_webp(
                frames,
                frame_duration_ms=options.frame_duration_ms,
                quality=options.webp_quality,
                lossless=options.webp_lossless,
            )
        return encode_gif(
            frames,
            frame_duration_ms=options.frame_duration_ms,
            dither=options.gif_dither,
        )
    finally:
        for frame in frames:
            frame.close()


def build_wigglegram_artifact(
    client: ImmichClient,
    group: WiggleGroup,
    *,
    options: ExportOptions,
    byte_cache: ImageByteCache | None = None,
) -> WigglegramArtifact:
    frames = build_wigglegram_frames(client, group, options=options, byte_cache=byte_cache)
    data = encode_wigglegram(frames, options=options)
    timestamp = group.assets[0].local_datetime.strftime("%Y-%m-%d_%H-%M-%S")
    if options.output_format == "webp":
        return WigglegramArtifact(
            data=data,
            filename=f"wiggle_{timestamp}.webp",
            mime_type="image/webp",
            output_format="webp",
        )
    return WigglegramArtifact(
        data=data,
        filename=f"wiggle_{timestamp}.gif",
        mime_type="image/gif",
        output_format="gif",
    )


def make_wigglegram_bytes(
    client: ImmichClient,
    group: WiggleGroup,
    *,
    frame_duration_ms: int = 100,
    max_size: int = 900,
    boomerang: bool = True,
    frame_fit: FrameFit = "letterbox",
    stabilize: StabilizeOptions | None = None,
    output_format: OutputFormat = "gif",
    webp_quality: int = 85,
    webp_lossless: bool = False,
    gif_dither: bool = True,
    download_workers: int = 4,
    byte_cache: ImageByteCache | None = None,
) -> bytes:
    options = ExportOptions(
        frame_duration_ms=frame_duration_ms,
        max_size=max_size,
        boomerang=boomerang,
        frame_fit=frame_fit,
        stabilize=stabilize,
        output_format=output_format,
        webp_quality=webp_quality,
        webp_lossless=webp_lossless,
        gif_dither=gif_dither,
        download_workers=download_workers,
    )
    artifact = build_wigglegram_artifact(
        client,
        group,
        options=options,
        byte_cache=byte_cache,
    )
    return artifact.data


def build_gif_filename(group: WiggleGroup) -> str:
    timestamp = group.assets[0].local_datetime.strftime("%Y-%m-%d_%H-%M-%S")
    return f"wiggle_{timestamp}.gif"


def build_export_filename(group: WiggleGroup, *, output_format: OutputFormat) -> str:
    timestamp = group.assets[0].local_datetime.strftime("%Y-%m-%d_%H-%M-%S")
    extension = "webp" if output_format == "webp" else "gif"
    return f"wiggle_{timestamp}.{extension}"


def export_wiggle_group(
    client: ImmichClient,
    group: WiggleGroup,
    *,
    frame_duration_ms: int,
    max_size: int,
    boomerang: bool,
    device_id: str,
    frame_fit: FrameFit = "letterbox",
    stabilize: StabilizeOptions | None = None,
    output_format: OutputFormat = "gif",
    webp_quality: int = 85,
    webp_lossless: bool = False,
    gif_dither: bool = True,
    download_workers: int = 4,
    byte_cache: ImageByteCache | None = None,
    device_asset_id_suffix: str = "",
) -> dict:
    options = ExportOptions(
        frame_duration_ms=frame_duration_ms,
        max_size=max_size,
        boomerang=boomerang,
        frame_fit=frame_fit,
        stabilize=stabilize,
        output_format=output_format,
        webp_quality=webp_quality,
        webp_lossless=webp_lossless,
        gif_dither=gif_dither,
        download_workers=download_workers,
    )
    artifact = build_wigglegram_artifact(
        client,
        group,
        options=options,
        byte_cache=byte_cache,
    )
    created_at = group.assets[0].local_datetime
    suffix = f"-{device_asset_id_suffix}" if device_asset_id_suffix else ""
    device_asset_id = f"wiggle-{group.group_key}{suffix}"

    return client.upload_asset(
        artifact.data,
        filename=artifact.filename,
        mime_type=artifact.mime_type,
        file_created_at=created_at,
        device_asset_id=device_asset_id,
        device_id=device_id,
    )
