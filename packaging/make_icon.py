"""Build packaging/nexusvox.ico (16 to 256 px) from the NexusVox logo kit files kept in the repository.

    python packaging/make_icon.py

The frames sit on the kit's dark #0d0d10 tile, so the icon reads on a light and a dark Explorer: 16 to 48 px come from
the tray tiles (``src/nexusvox/system/tray_icons/tile-N.png``, the pixel-fitted small drawing), 64 to 256 px from the
kit's app icon (``packaging/icon_sources/nexusvox-app-icon-N.png``). The .ico is committed so a build needs no
rendering step.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ICON = HERE / "nexusvox.ico"
TILES = HERE.parent / "src" / "nexusvox" / "system" / "tray_icons"
SOURCES = HERE / "icon_sources"
SMALL = (16, 24, 32, 48)
LARGE = (64, 128, 256)


def build() -> None:
    frames = [Image.open(TILES / f"tile-{n}.png").convert("RGBA") for n in SMALL]
    frames += [Image.open(SOURCES / f"nexusvox-app-icon-{n}.png").convert("RGBA") for n in LARGE]
    frames.sort(key=lambda frame: frame.width)
    # Pillow stores each frame given in ``append_images``; the first one is the base image.
    frames[-1].save(ICON, format="ICO", sizes=[frame.size for frame in frames], append_images=frames[:-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    build()
    print(f"Wrote {ICON} ({ICON.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
