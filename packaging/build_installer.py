"""Compile the Inno Setup installer from the one-folder build in dist/NexusVox.

    python packaging/build.py            # first: build the app
    python packaging/build_installer.py  # then: dist/installer/NexusVox-Setup.exe

    python packaging/build_installer.py --sign-command '$qC:/path/signtool.exe$q sign /fd SHA256 ... $f'
        # Inno Setup runs that command on the setup and on the uninstaller it embeds (`$f` is the file, `$q` a quote;
        # packaging/sign_release.ps1 builds the command). Without it nothing is signed.

The installer is named `NexusVox-Setup.exe` for every version. The website links to
`releases/latest/download/NexusVox-Setup.exe`, so keep the name stable. The version comes from `pyproject.toml`.

Inno Setup 6 is found through the ISCC environment variable, `PATH`, or its default install folders.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from third_party_notices import MissingLicenceError, write_notices

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


def numeric_version(version: str) -> str:
    """The leading numeric fields of a version, padded to four: Inno's VersionInfoVersion accepts nothing else.

    `1.0.0rc1` becomes `1.0.0.0`; the full string still goes to AppVersion, which takes any text.
    """
    match = re.match(r"\d+(?:\.\d+){0,3}", version)
    if match is None:
        raise ValueError(f"No numeric version in {version!r}")
    fields = match.group().split(".")
    return ".".join(fields + ["0"] * (4 - len(fields)))


def iscc_command(iscc: str, version: str, sign_command: str | None = None) -> list[str]:
    """The ISCC command line. A sign command defines the `nexusvox` sign tool and turns on `SignInstaller`, which makes
    the script sign the setup and the embedded uninstaller with it."""
    command = [iscc, f"/DAppVersion={version}", f"/DAppVersionInfo={numeric_version(version)}"]
    if sign_command:
        command += [f"/Snexusvox={sign_command}", "/DSignInstaller=1"]
    return [*command, str(SCRIPT)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sign-command", help="signtool command Inno Setup runs on the setup and the uninstaller")
    args = parser.parse_args()
    if not APP_EXE.is_file():
        print(f"{APP_EXE} not found. Run `python packaging/build.py` first.", file=sys.stderr)
        return 1
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup 6 not found. Install it (https://jrsoftware.org/isinfo.php) or set ISCC.", file=sys.stderr)
        return 1
    version = app_version()
    # The licence notices for everything the frozen app contains; the script shows and installs them.
    try:
        write_notices(ROOT / "dist" / "installer" / "THIRD_PARTY_NOTICES.txt")
    except MissingLicenceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    command = iscc_command(iscc, version, args.sign_command)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode
    print(f"Installer: {OUTPUT} ({OUTPUT.stat().st_size / 1_000_000:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
