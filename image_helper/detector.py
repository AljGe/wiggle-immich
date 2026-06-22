from __future__ import annotations

import imagehash

from image_helper.models import AssetRecord, WiggleGroup


def phash_distance(left: str, right: str) -> int:
  return imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right)


def find_wiggle_groups(
  assets: list[AssetRecord],
  *,
  threshold: int,
  time_window_seconds: float,
) -> list[WiggleGroup]:
  if len(assets) < 2:
    return []

  sorted_assets = sorted(assets, key=lambda asset: asset.local_datetime)
  groups: list[WiggleGroup] = []
  current_group: list[AssetRecord] = []
  current_distances: list[int] = []

  for index in range(1, len(sorted_assets)):
    previous = sorted_assets[index - 1]
    current = sorted_assets[index]
    distance = phash_distance(previous.phash, current.phash)
    time_gap = abs((current.local_datetime - previous.local_datetime).total_seconds())

    is_match = 0 < distance < threshold and time_gap <= time_window_seconds

    if is_match:
      if not current_group:
        current_group = [previous]
        current_distances = []
      current_group.append(current)
      current_distances.append(distance)
      continue

    if len(current_group) >= 2:
      groups.append(
        WiggleGroup(
          assets=tuple(current_group),
          distances=tuple(current_distances),
        )
      )
    current_group = []
    current_distances = []

  if len(current_group) >= 2:
    groups.append(
      WiggleGroup(
        assets=tuple(current_group),
        distances=tuple(current_distances),
      )
    )

  return groups
