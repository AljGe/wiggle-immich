from __future__ import annotations

from typing import Any


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _burst_id_from_exif(exif: dict[str, Any]) -> str | None:
    for key in (
        "burstUUID",
        "burstId",
        "mediaGroupUUID",
        "MediaGroupUUID",
        "BurstUUID",
    ):
        value = exif.get(key)
        if value:
            return str(value)
    return None


def _burst_sequence_from_exif(exif: dict[str, Any]) -> int | None:
    for key in ("imageNumber", "sequenceNumber", "burstSequence"):
        sequence = _positive_int(exif.get(key))
        if sequence is not None:
            return sequence
    return None


def extract_asset_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    exif = asset.get("exifInfo") or {}
    width = _positive_int(exif.get("exifImageWidth") or exif.get("imageWidth"))
    height = _positive_int(exif.get("exifImageHeight") or exif.get("imageHeight"))

    stack = asset.get("stack")
    stack_id: str | None = None
    is_primary_in_stack: bool | None = None

    if isinstance(stack, dict):
        stack_id = stack.get("id")
        primary_asset_id = stack.get("primaryAssetId")
        if primary_asset_id and asset.get("id"):
            is_primary_in_stack = primary_asset_id == asset["id"]
    elif asset.get("stackId"):
        stack_id = str(asset["stackId"])

    return {
        "width": width,
        "height": height,
        "original_file_name": asset.get("originalFileName"),
        "stack_id": stack_id,
        "is_primary_in_stack": is_primary_in_stack,
        "burst_id": _burst_id_from_exif(exif),
        "burst_sequence": _burst_sequence_from_exif(exif),
    }
