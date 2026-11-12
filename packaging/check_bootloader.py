"""Check that the installed PyInstaller carries a bootloader built from source, not the shared prebuilt one.

    python packaging/check_bootloader.py

The prebuilt bootloader is one binary that thousands of programs share, and antivirus engines flag it by its hash.
This compares the windowed and console bootloaders of the installed PyInstaller with the ones in the wheel that
PyPI publishes for the same version, and fails when they are byte-identical. The wheel is downloaded fresh (`pip
download --no-cache-dir`), so a stale pip cache cannot hide anything. It needs network access.

Exit codes: 0 rebuilt, 1 identical to the published bootloader, 2 could not compare.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import zipfile
from importlib import metadata
from pathlib import Path

PLATFORM_FOLDER = "Windows-64bit-intel"
STUBS = ("run.exe", "runw.exe")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identical_stubs(installed: dict[str, bytes], published: dict[str, bytes]) -> list[str]:
    """The names of the bootloaders that are byte-for-byte the published ones. Empty means all were rebuilt."""
    return sorted(
        name for name, data in installed.items() if name in published and digest(data) == digest(published[name])
    )


def installed_stubs() -> dict[str, bytes]:
    import PyInstaller

    folder = Path(PyInstaller.__file__).parent / "bootloader" / PLATFORM_FOLDER
    return {name: (folder / name).read_bytes() for name in STUBS}


def published_stubs(version: str) -> dict[str, bytes]:
    """The stubs from the wheel PyPI serves for this version (downloaded into a temporary folder)."""
    with tempfile.TemporaryDirectory() as folder:
        command = [
            sys.executable, "-m", "pip", "download", "--quiet", "--no-cache-dir", "--no-deps",
            "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
            "--dest", folder, f"pyinstaller=={version}",
        ]  # fmt: skip
        subprocess.run(command, check=True)
        wheel = next(Path(folder).glob("pyinstaller-*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            return {name: archive.read(f"PyInstaller/bootloader/{PLATFORM_FOLDER}/{name}") for name in STUBS}


def main() -> int:
    version = metadata.version("pyinstaller")
    try:
        installed, published = installed_stubs(), published_stubs(version)
    except (OSError, subprocess.CalledProcessError, StopIteration, KeyError) as error:
        print(f"Could not compare the bootloader of PyInstaller {version}: {error!r}", file=sys.stderr)
        return 2
    same = identical_stubs(installed, published)
    for name in STUBS:
        print(f"{name}: {digest(installed[name])[:16]} installed, {digest(published[name])[:16]} published")
    if same:
        print(
            f"PyInstaller {version} uses the shared prebuilt bootloader ({', '.join(same)}). Rebuild it: "
            '$env:PYINSTALLER_COMPILE_BOOTLOADER = "1"; pip install --no-cache-dir --force-reinstall '
            '--no-binary pyinstaller "pyinstaller>=6,<7"',
            file=sys.stderr,
        )
        return 1
    print(f"PyInstaller {version}: the bootloader was built from source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
