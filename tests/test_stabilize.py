from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from image_helper.stabilize import stabilize_frames


def _draw_burst_frame(
    *,
    size: tuple[int, int] = (480, 360),
    offset: int = 0,
    index: int = 0,
) -> Image.Image:
    frame = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(frame)
    draw.rectangle(
        [40 + offset, 40, size[0] - 40 + offset, size[1] - 40],
        fill="#e74c3c",
    )
    draw.ellipse(
        [140 + offset, 90 + index, 340 + offset, 250 + index],
        fill="#3498db",
    )
    draw.polygon(
        [(80 + offset, 300), (160 + offset, 180 - index), (240 + offset, 300)],
        fill="#2ecc71",
    )
    return frame


def _apply_jitter(frame: Image.Image, *, dx: int, dy: int, angle: float) -> Image.Image:
    jittered = frame.transform(
        frame.size,
        Image.Transform.AFFINE,
        (1, 0, dx, 0, 1, dy),
        resample=Image.Resampling.BICUBIC,
    )
    return jittered.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)


def _make_jittered_burst(
    *,
    count: int = 3,
    shift_pixels: int = 0,
    jitter_offsets: list[tuple[int, int, float]],
) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for index in range(count):
        base = _draw_burst_frame(offset=index * shift_pixels, index=index)
        dx, dy, angle = jitter_offsets[index % len(jitter_offsets)]
        frames.append(_apply_jitter(base, dx=dx, dy=dy, angle=angle))
        base.close()
    return frames


def _center_crop_gray(image: Image.Image) -> np.ndarray:
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    height, width = gray.shape
    margin_y = height // 5
    margin_x = width // 5
    return gray[margin_y : height - margin_y, margin_x : width - margin_x]


def _translation_error(frames: list[Image.Image], reference_index: int) -> float:
    reference_crop = _center_crop_gray(frames[reference_index])
    errors: list[float] = []
    for index, frame in enumerate(frames):
        if index == reference_index:
            continue
        frame_crop = _center_crop_gray(frame)
        shift, _ = cv2.phaseCorrelate(reference_crop, frame_crop)
        errors.append(float(np.hypot(shift[0], shift[1])))
    return float(np.mean(errors))


def test_stabilize_reduces_jitter_alignment_error() -> None:
    jitter_offsets = [(0, 0, 0.0), (8, -6, 1.5), (-5, 7, -1.0)]
    frames = _make_jittered_burst(jitter_offsets=jitter_offsets)
    reference_index = len(frames) // 2
    before = _translation_error(frames, reference_index)

    stabilized = stabilize_frames(
        frames,
        mode="translate",
        reference="middle",
        crop_to_overlap=False,
    )
    try:
        after = _translation_error(stabilized, reference_index)
        assert before > 2.0
        assert after < before
        assert after < 2.0
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()


def test_stabilize_corrects_known_translation() -> None:
    reference = _draw_burst_frame()
    shifted = reference.copy()
    shifted = shifted.transform(
        shifted.size,
        Image.Transform.AFFINE,
        (1, 0, 12, 0, 1, -7),
        resample=Image.Resampling.BICUBIC,
    )
    frames = [reference, shifted]

    stabilized = stabilize_frames(
        frames,
        mode="translate",
        reference="first",
        crop_to_overlap=False,
    )
    try:
        assert _translation_error(stabilized, 0) < 1.5
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()


def _endpoint_horizontal_shift(frames: list[Image.Image]) -> float:
    if len(frames) < 2:
        return 0.0
    left_crop = _center_crop_gray(frames[0])
    right_crop = _center_crop_gray(frames[-1])
    shift, _ = cv2.phaseCorrelate(left_crop, right_crop)
    return abs(float(shift[0]))


def test_stabilize_preserves_horizontal_parallax() -> None:
    frames = [
        _draw_burst_frame(offset=0, index=0),
        _draw_burst_frame(offset=8, index=1),
        _draw_burst_frame(offset=16, index=2),
    ]
    before_shift = _endpoint_horizontal_shift(frames)
    stabilized = stabilize_frames(
        frames,
        mode="auto",
        reference="middle",
        crop_to_overlap=False,
    )
    try:
        after_shift = _endpoint_horizontal_shift(stabilized)
        assert before_shift > 4.0
        assert after_shift > 2.0
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()


def test_stabilize_off_returns_copies() -> None:
    frames = _make_jittered_burst(jitter_offsets=[(0, 0, 0.0), (4, 2, 1.0), (2, -3, -1.0)])
    stabilized = stabilize_frames(frames, mode="off")
    try:
        assert len(stabilized) == len(frames)
        assert stabilized[0] is not frames[0]
        assert stabilized[0].size == frames[0].size
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()


def test_stabilize_single_frame_is_unchanged() -> None:
    frame = _draw_burst_frame()
    stabilized = stabilize_frames([frame], mode="auto")
    try:
        assert len(stabilized) == 1
        assert stabilized[0].size == frame.size
    finally:
        frame.close()
        stabilized[0].close()


def test_horizontal_mode_does_not_change_vertical_offset() -> None:
    frames = _make_jittered_burst(jitter_offsets=[(0, 0, 0.0), (6, -8, 0.0), (-4, 10, 0.0)])
    stabilized = stabilize_frames(
        frames,
        mode="horizontal",
        reference="first",
        crop_to_overlap=False,
    )
    try:
        reference_crop = _center_crop_gray(stabilized[0])
        for frame in stabilized[1:]:
            frame_crop = _center_crop_gray(frame)
            shift, _ = cv2.phaseCorrelate(reference_crop, frame_crop)
            assert abs(float(shift[1])) > 2.0
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()


def test_stabilize_corrects_known_translation_at_high_resolution() -> None:
    """Regression: alignment must work when frames exceed working_max_edge."""
    size = (2400, 1800)
    reference = _draw_burst_frame(size=size)
    shifted = reference.copy()
    shifted = shifted.transform(
        shifted.size,
        Image.Transform.AFFINE,
        (1, 0, 12, 0, 1, -7),
        resample=Image.Resampling.BICUBIC,
    )
    frames = [reference, shifted]

    stabilized = stabilize_frames(
        frames,
        mode="translate",
        reference="first",
        crop_to_overlap=False,
        working_max_edge=1024,
    )
    try:
        assert _translation_error(stabilized, 0) < 1.5
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()


def test_stabilize_identical_frames_do_not_crash() -> None:
    base = _draw_burst_frame()
    frames = [base.copy(), base.copy(), base.copy()]
    base.close()
    stabilized = stabilize_frames(frames, mode="auto", crop_to_overlap=False)
    try:
        assert len(stabilized) == 3
    finally:
        for frame in frames:
            frame.close()
        for frame in stabilized:
            frame.close()
