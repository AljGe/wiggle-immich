#!/usr/bin/env python3
"""Generate synthetic burst frames and negative edit fixtures for tests."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def generate_burst_frames(
    output_dir: Path,
    *,
    count: int = 3,
    shift_pixels: int = 4,
    size: tuple[int, int] = (480, 360),
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for index in range(count):
        frame = Image.new("RGB", size, "white")
        draw_frame = ImageDraw.Draw(frame)
        offset = index * shift_pixels
        draw_frame.rectangle(
            [40 + offset, 40, size[0] - 40 + offset, size[1] - 40],
            fill="#e74c3c",
        )
        draw_frame.ellipse(
            [140 + offset, 90 + index, 340 + offset, 250 + index],
            fill="#3498db",
        )
        draw_frame.polygon(
            [(80 + offset, 300), (160 + offset, 180 - index), (240 + offset, 300)],
            fill="#2ecc71",
        )
        path = output_dir / f"wiggle_burst_{index:02d}.png"
        frame.save(path, format="PNG")
        paths.append(path)

    control = Image.new("RGB", size, "white")
    control_draw = ImageDraw.Draw(control)
    control_draw.rectangle([40, 40, size[0] - 40, size[1] - 40], fill="#9b59b6")
    control_path = output_dir / "control_unrelated.png"
    control.save(control_path, format="PNG")
    paths.append(control_path)

    return paths


def generate_edit_fixtures(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    base_size = (480, 360)
    original = Image.new("RGB", base_size, "white")
    draw = ImageDraw.Draw(original)
    draw.rectangle([40, 40, base_size[0] - 40, base_size[1] - 40], fill="#e74c3c")
    draw.ellipse([140, 90, 340, 250], fill="#3498db")

    original_path = output_dir / "edit_original.png"
    original.save(original_path, format="PNG")
    paths.append(original_path)

    cropped = original.crop((80, 40, 400, 320))
    crop_path = output_dir / "edit_crop.png"
    cropped.save(crop_path, format="PNG")
    paths.append(crop_path)

    rotated = original.rotate(90, expand=True)
    rotate_path = output_dir / "edit_rotate.png"
    rotated.save(rotate_path, format="PNG")
    paths.append(rotate_path)

    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="testdata/fixtures",
        type=Path,
        help="Directory for generated PNG fixtures",
    )
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--shift", type=int, default=4)
    parser.add_argument(
        "--with-edits",
        action="store_true",
        help="Also generate negative edit fixtures under <output_dir>/edits",
    )
    args = parser.parse_args()

    paths = generate_burst_frames(args.output_dir, count=args.count, shift_pixels=args.shift)
    if args.with_edits:
        paths.extend(generate_edit_fixtures(args.output_dir / "edits"))

    print(f"Generated {len(paths)} images in {args.output_dir.resolve()}")
    for path in paths:
        print(f"  - {path.name}")


if __name__ == "__main__":
    main()
