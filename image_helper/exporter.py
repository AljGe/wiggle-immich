from __future__ import annotations

import io
from datetime import datetime

from PIL import Image

from image_helper.immich import ImmichClient
from image_helper.models import AssetRecord, WiggleGroup


def make_wigglegram_bytes(
  client: ImmichClient,
  group: WiggleGroup,
  *,
  frame_duration_ms: int = 100,
  max_size: int = 600,
  boomerang: bool = True,
) -> bytes:
  frames: list[Image.Image] = []
  try:
    for asset in group.assets:
      data = client.download_original(asset.asset_id)
      image = Image.open(io.BytesIO(data))
      image.load()
      rgb = image.convert("RGB")
      rgb.thumbnail((max_size, max_size))
      frames.append(rgb)

    if boomerang and len(frames) > 1:
      frames = frames + list(reversed(frames))[1:]

  except Exception:
    for frame in frames:
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
) -> dict:
  gif_bytes = make_wigglegram_bytes(
    client,
    group,
    frame_duration_ms=frame_duration_ms,
    max_size=max_size,
    boomerang=boomerang,
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
