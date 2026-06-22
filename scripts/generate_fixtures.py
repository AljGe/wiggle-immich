#!/usr/bin/env python3
"""Generate synthetic burst frames that should group as a wiggle sequence."""

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

    base = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(base)
    draw.rectangle([40, 40, size[0] - 40, size[1] - 40], fill="#e74c3c")
    draw.ellipse([140, 90, 340, 250], fill="#3498db")
    draw.polygon([(80, 300), (160, 180), (240, 300)], fill="#2ecc71")

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

    # Control image: visually similar palette but far in time; should not group.
    control = Image.new("RGB", size, "white")
    control_draw = ImageDraw.Draw(control)
    control_draw.rectangle([40, 40, size[0] - 40, size[1] - 40], fill="#9b59b6")
    control_path = output_dir / "control_unrelated.png"
    control.save(control_path, format="PNG")
    paths.append(control_path)

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
    args = parser.parse_args()

    paths = generate_burst_frames(args.output_dir, count=args.count, shift_pixels=args.shift)
    print(f"Generated {len(paths)} images in {args.output_dir.resolve()}")
    for path in paths:
        print(f"  - {path.name}")


if __name__ == "__main__":
    main()
