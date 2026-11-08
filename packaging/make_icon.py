"""Build packaging/nexusvox.ico (16 to 256 px) from the NexusVox logo kit.

    python packaging/make_icon.py <path to the logo kit>

The frames sit on the kit's dark #0d0d10 tile, so the icon reads on a light and a dark Explorer: 16 to 48 px come from
``tray/png/tile-N.png`` (the pixel-fitted small drawing), 64 to 256 px from ``png/app-icon/nexusvox-app-icon-N.png``.
The .ico is committed so a build needs no kit and no rendering step.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ICON = Path(__file__).resolve().parent / "nexusvox.ico"
SMALL = (16, 24, 32, 48)
LARGE = (64, 128, 256)


def build(kit: Path) -> None:
    frames = [Image.open(kit / "tray" / "png" / f"tile-{n}.png").convert("RGBA") for n in SMALL]
    frames += [Image.open(kit / "png" / "app-icon" / f"nexusvox-app-icon-{n}.png").convert("RGBA") for n in LARGE]
    frames.sort(key=lambda frame: frame.width)
    # Pillow stores each frame given in ``append_images``; the first one is the base image.
    frames[-1].save(ICON, format="ICO", sizes=[frame.size for frame in frames], append_images=frames[:-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kit", type=Path, help="folder of the NexusVox logo kit")
    build(parser.parse_args().kit)
    print(f"Wrote {ICON} ({ICON.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
