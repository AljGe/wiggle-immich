from __future__ import annotations

import imagehash

from image_helper.models import AssetRecord, WiggleGroup

_phash_cache: dict[str, imagehash.ImageHash] = {}


def _cached_phash(phash: str) -> imagehash.ImageHash:
    cached = _phash_cache.get(phash)
    if cached is None:
        cached = imagehash.hex_to_hash(phash)
        _phash_cache[phash] = cached
    return cached


def clear_phash_cache() -> None:
    _phash_cache.clear()


def phash_distance(left: str, right: str) -> int:
    return _cached_phash(left) - _cached_phash(right)


def _time_gap_seconds(left: AssetRecord, right: AssetRecord) -> float:
    return abs((right.local_datetime - left.local_datetime).total_seconds())


def _phash_matches(left: AssetRecord, right: AssetRecord, *, threshold: int) -> bool:
    distance = phash_distance(left.phash, right.phash)
    return 0 < distance < threshold


def _burst_allows_link(left: AssetRecord, right: AssetRecord) -> bool:
    if left.burst_id and right.burst_id:
        return left.burst_id == right.burst_id
    return True


def _build_adjacency(
    sorted_assets: list[AssetRecord],
    *,
    threshold: int,
    time_window_seconds: float,
    max_gap_frames: int,
) -> list[list[int]]:
    size = len(sorted_assets)
    adjacency = [[] for _ in range(size)]

    for left_index in range(size):
        left = sorted_assets[left_index]
        for right_index in range(left_index + 1, size):
            right = sorted_assets[right_index]
            gap_frames = right_index - left_index - 1
            if gap_frames > max_gap_frames:
                break

            if _time_gap_seconds(left, right) > time_window_seconds:
                break

            if not _burst_allows_link(left, right):
                continue

            if _phash_matches(left, right, threshold=threshold):
                adjacency[left_index].append(right_index)
                adjacency[right_index].append(left_index)

    return adjacency


def _connected_components(adjacency: list[list[int]]) -> list[list[int]]:
    size = len(adjacency)
    visited = [False] * size
    components: list[list[int]] = []

    for start in range(size):
        if visited[start]:
            continue

        stack = [start]
        visited[start] = True
        component: list[int] = []

        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in adjacency[node]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)

        if len(component) >= 2:
            components.append(sorted(component))

    return components


def _component_to_group(
    sorted_assets: list[AssetRecord],
    indices: list[int],
) -> WiggleGroup:
    members = tuple(sorted_assets[index] for index in sorted(indices, key=lambda i: sorted_assets[i].local_datetime))
    distances = tuple(
        phash_distance(members[index - 1].phash, members[index].phash)
        for index in range(1, len(members))
    )
    return WiggleGroup(assets=members, distances=distances)


def find_wiggle_groups(
    assets: list[AssetRecord],
    *,
    threshold: int,
    time_window_seconds: float,
    max_gap_frames: int = 0,
) -> list[WiggleGroup]:
    if len(assets) < 2:
        return []

    for asset in assets:
        _cached_phash(asset.phash)

    sorted_assets = sorted(assets, key=lambda asset: asset.local_datetime)
    adjacency = _build_adjacency(
        sorted_assets,
        threshold=threshold,
        time_window_seconds=time_window_seconds,
        max_gap_frames=max_gap_frames,
    )
    components = _connected_components(adjacency)
    return [_component_to_group(sorted_assets, indices) for indices in components]
