"""Compile the Inno Setup installer from the one-folder build in dist/NexusVox.

    python packaging/build.py            # first: build the app
    python packaging/build_installer.py  # then: dist/installer/NexusVox-Setup.exe

The installer is named `NexusVox-Setup.exe` for every version. The website links to
`releases/latest/download/NexusVox-Setup.exe`, so keep the name stable. The version comes from `pyproject.toml`.

Inno Setup 6 is found through the ISCC environment variable, `PATH`, or its default install folders.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "nexusvox.iss"
APP_EXE = ROOT / "dist" / "NexusVox" / "NexusVox.exe"
OUTPUT = ROOT / "dist" / "installer" / "NexusVox-Setup.exe"


def find_iscc() -> str | None:
    """The Inno Setup command-line compiler, or None."""
    if os.environ.get("ISCC"):
        return os.environ["ISCC"]
    on_path = shutil.which("ISCC")
    if on_path:
        return on_path
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if not base:
            continue
        for folder in ("Inno Setup 6", r"Programs\Inno Setup 6"):
            candidate = Path(base) / folder / "ISCC.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def app_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def main() -> int:
    if not APP_EXE.is_file():
        print(f"{APP_EXE} not found. Run `python packaging/build.py` first.", file=sys.stderr)
        return 1
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup 6 not found. Install it (https://jrsoftware.org/isinfo.php) or set ISCC.", file=sys.stderr)
        return 1
    result = subprocess.run([iscc, f"/DAppVersion={app_version()}", str(SCRIPT)], cwd=ROOT)
    if result.returncode != 0:
        return result.returncode
    print(f"Installer: {OUTPUT} ({OUTPUT.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
