from __future__ import annotations

from dataclasses import dataclass

from image_helper.config import Settings
from image_helper.models import AssetRecord, WiggleGroup

WIGGLE_EXPORT_FILENAME_PREFIX = "wiggle_"


@dataclass(frozen=True)
class RejectedWiggleGroup:
    group: WiggleGroup
    reason: str


def _timestamps_non_progressive(assets: tuple[AssetRecord, ...]) -> bool:
    timestamps = [asset.local_datetime for asset in assets]
    if len(set(timestamps)) == 1:
        return True
    return all(
        timestamps[index] <= timestamps[index - 1]
        for index in range(1, len(timestamps))
    )


def _dimension_drift(assets: tuple[AssetRecord, ...]) -> float | None:
    widths = [asset.width for asset in assets if asset.width is not None]
    heights = [asset.height for asset in assets if asset.height is not None]
    if len(widths) < 2 or len(heights) < 2:
        return None

    width_drift = (max(widths) - min(widths)) / max(max(widths), 1)
    height_drift = (max(heights) - min(heights)) / max(max(heights), 1)
    return max(width_drift, height_drift)


def _has_shared_stack(assets: tuple[AssetRecord, ...]) -> bool:
    stack_ids = [asset.stack_id for asset in assets if asset.stack_id]
    return len(stack_ids) >= 2 and len(set(stack_ids)) < len(stack_ids)


def _contains_exported_wiggle_asset(assets: tuple[AssetRecord, ...]) -> bool:
    for asset in assets:
        name = asset.original_file_name or ""
        if name.startswith(WIGGLE_EXPORT_FILENAME_PREFIX):
            return True
    return False


def _burst_metadata_issue(assets: tuple[AssetRecord, ...], settings: Settings) -> str | None:
    burst_ids = [asset.burst_id for asset in assets if asset.burst_id]
    if settings.wiggle_require_burst_metadata:
        if len(burst_ids) < len(assets):
            return "missing burst metadata"
        if len(set(burst_ids)) > 1:
            return "mixed burst identifiers"
        return None

    distinct_burst_ids = {asset.burst_id for asset in assets if asset.burst_id}
    if len(distinct_burst_ids) > 1:
        return "mixed burst identifiers"
    return None


def validate_wiggle_group(group: WiggleGroup, settings: Settings) -> str | None:
    if len(group.assets) < settings.wiggle_min_frames:
        return f"fewer than {settings.wiggle_min_frames} frames"

    if _contains_exported_wiggle_asset(group.assets):
        return "contains previously exported wiggle asset"

    burst_issue = _burst_metadata_issue(group.assets, settings)
    if burst_issue is not None:
        return burst_issue

    if settings.wiggle_exclude_stacked and _has_shared_stack(group.assets):
        return "members share an Immich stack"

    drift = _dimension_drift(group.assets)
    if drift is not None and drift > settings.wiggle_max_dimension_drift:
        return f"dimension drift {drift:.1%} exceeds {settings.wiggle_max_dimension_drift:.1%}"

    if group.distances and all(
        distance < settings.wiggle_min_distance for distance in group.distances
    ):
        if _timestamps_non_progressive(group.assets):
            return "edit-like similarity with non-progressive timestamps"

    return None


def partition_wiggle_groups(
    groups: list[WiggleGroup],
    settings: Settings,
) -> tuple[list[WiggleGroup], list[RejectedWiggleGroup]]:
    accepted: list[WiggleGroup] = []
    rejected: list[RejectedWiggleGroup] = []

    for group in groups:
        reason = validate_wiggle_group(group, settings)
        if reason is None:
            accepted.append(group)
        else:
            rejected.append(RejectedWiggleGroup(group=group, reason=reason))

    return accepted, rejected
