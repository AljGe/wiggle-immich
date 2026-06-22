from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from image_helper.frames import FrameFit, decode_image_rgb, normalize_frames_for_gif
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


def make_wigglegram_bytes(
    client: ImmichClient,
    group: WiggleGroup,
    *,
    frame_duration_ms: int = 100,
    max_size: int = 600,
    boomerang: bool = True,
    frame_fit: FrameFit = "letterbox",
    stabilize: StabilizeOptions | None = None,
) -> bytes:
    stabilize = stabilize or StabilizeOptions()
    raw_frames: list[Image.Image] = []
    try:
        for asset in group.assets:
            data = client.download_original(asset.asset_id)
            raw_frames.append(decode_image_rgb(data))

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

        frames = normalize_frames_for_gif(
            raw_frames,
            max_size=max_size,
            frame_fit=frame_fit,
        )

        if boomerang and len(frames) > 1:
            frames = frames + list(reversed(frames))[1:]

    except Exception:
        for frame in raw_frames:
            frame.close()
        raise

    buffer = io.BytesIO()
    try:
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=frame_duration_ms,
            loop=0,
        )
        return buffer.getvalue()
    finally:
        for frame in frames:
            frame.close()


def build_gif_filename(group: WiggleGroup) -> str:
    timestamp = group.assets[0].local_datetime.strftime("%Y-%m-%d_%H-%M-%S")
    return f"wiggle_{timestamp}.gif"


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
) -> dict:
    gif_bytes = make_wigglegram_bytes(
        client,
        group,
        frame_duration_ms=frame_duration_ms,
        max_size=max_size,
        boomerang=boomerang,
        frame_fit=frame_fit,
        stabilize=stabilize,
    )
    filename = build_gif_filename(group)
    created_at = group.assets[0].local_datetime
    device_asset_id = f"wiggle-{group.group_key}"

    return client.upload_gif(
        gif_bytes,
        filename=filename,
        file_created_at=created_at,
        device_asset_id=device_asset_id,
        device_id=device_id,
    )
