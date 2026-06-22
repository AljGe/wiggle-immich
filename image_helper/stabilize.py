from __future__ import annotations

import logging
import math
from typing import Literal

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

StabilizeMode = Literal["auto", "rigid", "translate", "horizontal", "off"]
StabilizeReference = Literal["first", "middle"]

_MIN_ORB_MATCHES = 8
_CENTER_ROI_FRACTION = 0.4
_MAX_SCALE_DRIFT = 0.05


def _reference_index(frame_count: int, reference: StabilizeReference) -> int:
    if reference == "first" or frame_count <= 1:
        return 0
    return frame_count // 2


def _resize_for_working(image: Image.Image, max_edge: int) -> tuple[Image.Image, float]:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image.copy(), 1.0
    scale = max_edge / longest
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS), scale


def _to_gray_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"))


def _rotation_degrees(matrix: np.ndarray) -> float:
    return math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))


def _scale_drift(matrix: np.ndarray) -> float:
    scale = math.hypot(matrix[0, 0], matrix[1, 0])
    return abs(scale - 1.0)


def _is_valid_transform(matrix: np.ndarray, *, max_rotation_deg: float) -> bool:
    if not np.isfinite(matrix).all():
        return False
    if abs(_rotation_degrees(matrix)) > max_rotation_deg:
        return False
    if _scale_drift(matrix) > _MAX_SCALE_DRIFT:
        return False
    return True


def _shake_only_matrix(matrix: np.ndarray) -> np.ndarray:
    """Keep rotation/scale/vertical correction; preserve horizontal parallax."""
    return np.array(
        [
            [matrix[0, 0], matrix[0, 1], 0.0],
            [matrix[1, 0], matrix[1, 1], matrix[1, 2]],
        ],
        dtype=np.float32,
    )


def _constrain_matrix(matrix: np.ndarray, mode: StabilizeMode) -> np.ndarray:
    constrained = matrix.copy()
    if mode == "horizontal":
        constrained[1, 0] = 0.0
        constrained[1, 1] = 1.0
        constrained[0, 1] = 0.0
        constrained[0, 0] = 1.0
        constrained[1, 2] = 0.0
    elif mode == "translate":
        constrained[0, 0] = 1.0
        constrained[0, 1] = 0.0
        constrained[1, 0] = 0.0
        constrained[1, 1] = 1.0
    return constrained


def _create_orb_detector() -> cv2.ORB:
    return cv2.ORB_create(nfeatures=2000)


def _match_keypoints(
    reference_gray: np.ndarray,
    frame_gray: np.ndarray,
    *,
    center_roi: bool,
    detector: cv2.ORB,
    matcher: cv2.BFMatcher,
) -> tuple[np.ndarray, np.ndarray]:
    ref_keypoints, ref_descriptors = detector.detectAndCompute(reference_gray, None)
    frame_keypoints, frame_descriptors = detector.detectAndCompute(frame_gray, None)
    if (
        ref_descriptors is None
        or frame_descriptors is None
        or len(ref_keypoints) < _MIN_ORB_MATCHES
        or len(frame_keypoints) < _MIN_ORB_MATCHES
    ):
        return np.empty((0, 2), dtype=np.float32), np.empty((0, 2), dtype=np.float32)

    matches = matcher.match(ref_descriptors, frame_descriptors)
    if len(matches) < _MIN_ORB_MATCHES:
        return np.empty((0, 2), dtype=np.float32), np.empty((0, 2), dtype=np.float32)

    ref_points: list[list[float]] = []
    frame_points: list[list[float]] = []
    height, width = reference_gray.shape[:2]
    if center_roi:
        margin_x = width * (1.0 - _CENTER_ROI_FRACTION) / 2.0
        margin_y = height * (1.0 - _CENTER_ROI_FRACTION) / 2.0
        x_min = margin_x
        x_max = width - margin_x
        y_min = margin_y
        y_max = height - margin_y
    else:
        x_min = y_min = 0.0
        x_max = float(width)
        y_max = float(height)

    for match in matches:
        ref_point = ref_keypoints[match.queryIdx].pt
        frame_point = frame_keypoints[match.trainIdx].pt
        if not (x_min <= ref_point[0] <= x_max and y_min <= ref_point[1] <= y_max):
            continue
        ref_points.append([ref_point[0], ref_point[1]])
        frame_points.append([frame_point[0], frame_point[1]])

    if len(ref_points) < _MIN_ORB_MATCHES:
        return np.empty((0, 2), dtype=np.float32), np.empty((0, 2), dtype=np.float32)

    return np.asarray(ref_points, dtype=np.float32), np.asarray(frame_points, dtype=np.float32)


def _estimate_affine(
    reference_gray: np.ndarray,
    frame_gray: np.ndarray,
    *,
    mode: StabilizeMode,
    max_rotation_deg: float,
    detector: cv2.ORB,
    matcher: cv2.BFMatcher,
) -> np.ndarray | None:
    ref_points, frame_points = _match_keypoints(
        reference_gray,
        frame_gray,
        center_roi=False,
        detector=detector,
        matcher=matcher,
    )
    if len(ref_points) >= _MIN_ORB_MATCHES:
        matrix, _inliers = cv2.estimateAffinePartial2D(
            frame_points,
            ref_points,
            method=cv2.RANSAC,
            ransacReprojThreshold=3.0,
        )
        if matrix is not None:
            matrix = _constrain_matrix(matrix, mode)
            if _is_valid_transform(matrix, max_rotation_deg=max_rotation_deg):
                return matrix

    warp_matrix = np.eye(2, 3, dtype=np.float32)
    motion = cv2.MOTION_TRANSLATION if mode in {"translate", "horizontal"} else cv2.MOTION_EUCLIDEAN
    try:
        _, warp_matrix = cv2.findTransformECC(
            reference_gray,
            frame_gray,
            warp_matrix,
            motion,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5),
        )
    except cv2.error:
        return None

    warp_matrix = _constrain_matrix(warp_matrix, mode)
    if not _is_valid_transform(warp_matrix, max_rotation_deg=max_rotation_deg):
        return None
    return warp_matrix


def _warp_image(image: Image.Image, matrix: np.ndarray, output_size: tuple[int, int]) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"))
    warp_matrix = cv2.invertAffineTransform(matrix)
    warped = cv2.warpAffine(
        rgb,
        warp_matrix,
        output_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    return Image.fromarray(warped)


def _shift_image_horizontal(image: Image.Image, shift_x: int) -> Image.Image:
    if shift_x == 0:
        return image.copy()
    matrix = np.array([[1.0, 0.0, float(shift_x)], [0.0, 1.0, 0.0]], dtype=np.float32)
    return _warp_image(image, matrix, image.size)


def _content_mask(image: Image.Image) -> np.ndarray:
    gray = _to_gray_array(image)
    return gray > 8


def _crop_to_overlap(frames: list[Image.Image]) -> list[Image.Image]:
    if not frames:
        return []

    combined = np.ones(_content_mask(frames[0]), dtype=bool)
    for frame in frames[1:]:
        combined &= _content_mask(frame)

    if not combined.any():
        return [frame.copy() for frame in frames]

    rows = np.where(combined.any(axis=1))[0]
    cols = np.where(combined.any(axis=0))[0]
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    return [frame.crop((left, top, right, bottom)) for frame in frames]


def _apply_stereo_anchor(
    frames: list[Image.Image],
    *,
    reference_index: int,
    detector: cv2.ORB,
    matcher: cv2.BFMatcher,
) -> list[Image.Image]:
    reference_gray = _to_gray_array(frames[reference_index])
    disparities_by_index: dict[int, float] = {}

    for index, frame in enumerate(frames):
        if index == reference_index:
            continue
        ref_points, frame_points = _match_keypoints(
            reference_gray,
            _to_gray_array(frame),
            center_roi=True,
            detector=detector,
            matcher=matcher,
        )
        if len(ref_points) == 0:
            continue
        horizontal = ref_points[:, 0] - frame_points[:, 0]
        disparities_by_index[index] = float(np.median(horizontal))

    if not disparities_by_index:
        return frames

    global_median = float(np.median(list(disparities_by_index.values())))
    anchored: list[Image.Image] = []
    for index, frame in enumerate(frames):
        if index == reference_index:
            anchored.append(frame)
            continue
        frame_median = disparities_by_index.get(index, global_median)
        shift_x = int(round(global_median - frame_median))
        shifted = _shift_image_horizontal(frame, shift_x)
        frame.close()
        anchored.append(shifted)
    return anchored


def stabilize_frames(
    frames: list[Image.Image],
    *,
    mode: StabilizeMode = "auto",
    reference: StabilizeReference = "middle",
    crop_to_overlap: bool = True,
    max_rotation_deg: float = 3.0,
    working_max_edge: int = 1024,
) -> list[Image.Image]:
    if mode == "off" or len(frames) <= 1:
        return [frame.copy() for frame in frames]

    align_mode: StabilizeMode = "rigid" if mode == "auto" else mode
    reference_index = _reference_index(len(frames), reference)

    working_frames: list[Image.Image] = []
    try:
        for frame in frames:
            working, _ = _resize_for_working(frame, working_max_edge)
            working_frames.append(working)

        output_size = working_frames[reference_index].size
        reference_gray = _to_gray_array(working_frames[reference_index])

        detector = _create_orb_detector()
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

        aligned: list[Image.Image | None] = [None] * len(working_frames)
        aligned[reference_index] = working_frames[reference_index].copy()

        for index, frame in enumerate(working_frames):
            if index == reference_index:
                continue

            frame_gray = _to_gray_array(frame)
            matrix = _estimate_affine(
                reference_gray,
                frame_gray,
                mode=align_mode,
                max_rotation_deg=max_rotation_deg,
                detector=detector,
                matcher=matcher,
            )

            if matrix is None:
                logger.warning("Stabilization skipped for frame %s: alignment failed", index)
                aligned[index] = frame.copy()
                continue

            if mode == "auto":
                matrix = _shake_only_matrix(matrix)

            aligned[index] = _warp_image(frame, matrix, output_size)

        result = [frame for frame in aligned if frame is not None]

        if mode == "auto":
            anchored = _apply_stereo_anchor(
                result,
                reference_index=reference_index,
                detector=detector,
                matcher=matcher,
            )
            for index, frame in enumerate(result):
                if anchored[index] is not frame:
                    frame.close()
            result = anchored

        if crop_to_overlap:
            cropped = _crop_to_overlap(result)
            for frame in result:
                frame.close()
            result = cropped

        return result
    finally:
        for frame in working_frames:
            frame.close()
