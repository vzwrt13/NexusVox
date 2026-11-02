"""Build the one-folder Windows app with PyInstaller and report its size and build time.

    python packaging/build.py              # build into dist/NexusVox
    python packaging/build.py --clean      # drop PyInstaller's cache first
    python packaging/build.py --selftest   # test build in dist/selftest with `--selftest <dir>`; never ship it

Run it with the Python environment that has NexusVox and PyInstaller installed. Install NexusVox as a
regular package (`pip install .`), not editable: the build must contain what a wheel contains.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "nexusvox.spec"


def folder_size_mb(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true", help="build in the --selftest mode (never ship that build)")
    parser.add_argument("--clean", action="store_true", help="remove PyInstaller's cache before building")
    args = parser.parse_args()

    # A self-test build goes to its own folder so it can never be mistaken for the build that ships.
    dist = ROOT / "dist" / "selftest" if args.selftest else ROOT / "dist"
    command = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--noconfirm",
        "--distpath", str(dist),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
    ]  # fmt: skip
    if args.clean:
        command.append("--clean")

    env = dict(os.environ)
    if args.selftest:
        env["NEXUSVOX_BUILD_SELFTEST"] = "1"
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, env=env)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        return result.returncode

    app = dist / "NexusVox"
    print(f"Build time: {elapsed:.0f} s")
    print(f"Folder size: {folder_size_mb(app):.0f} MB ({app})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
