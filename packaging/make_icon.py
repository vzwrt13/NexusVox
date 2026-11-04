"""Render the NexusVox logo into packaging/nexusvox.ico (16 to 256 px).

    python packaging/make_icon.py

The geometry mirrors src/nexusvox/dashboard/static/logo.svg (64 px grid: ring r=22, stroke 7, round caps,
~78 degree gap, dot r=8), the same drawing the tray uses. The .ico is committed so a build needs no rendering step.
"""

from __future__ import annotations

import argparse
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw

ICON = Path(__file__).resolve().parent / "nexusvox.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
RING = "#6c5ce7"
DOT = "#00d2a0"


def render(size: int = 1024) -> Image.Image:
    unit = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = size / 2
    ring_r, stroke, dot_r = 22 * unit, round(7 * unit), 8 * unit
    start, end = -70, 211
    draw.arc([c - ring_r, c - ring_r, c + ring_r, c + ring_r], start=start, end=end, fill=RING, width=stroke)
    for angle in (start, end):
        x = c + ring_r * math.cos(math.radians(angle))
        y = c + ring_r * math.sin(math.radians(angle))
        draw.ellipse([x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2], fill=RING)
    draw.ellipse([c - dot_r, c - dot_r, c + dot_r, c + dot_r], fill=DOT)
    return img


def build() -> bytes:
    buffer = io.BytesIO()
    render().save(buffer, format="ICO", sizes=SIZES)
    return buffer.getvalue()


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    data = build()
    ICON.write_bytes(data)
    print(f"Wrote {ICON} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
