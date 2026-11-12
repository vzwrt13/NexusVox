"""Put a PyAV without GPL parts into the build environment, and check a built app for GPL parts.

    python packaging/lgpl_av.py            # replace the installed PyAV unless it already is LGPL-only
    python packaging/lgpl_av.py --check    # only report: exit 1 when the installed PyAV carries GPL parts

The PyAV wheels on PyPI bundle an FFmpeg built with x264 and x265, which are GPL; an installer that ships them falls
under the GPL (https://ffmpeg.org/legal.html), which NexusVox's source-available licence cannot follow. NexusVox
only decodes audio (uploads, and faster-whisper's own loader), so it needs none of them. This script builds the
same PyAV release from its source distribution against BtbN's LGPL build of FFmpeg (the `lgpl-shared` variant:
no `--enable-gpl`, no x264, x265, xvid or other GPL-only library), bundles the FFmpeg DLLs into the wheel with
delvewheel, as the PyPI wheel does, and installs it in place of the PyPI one. `packaging/build.py` runs it before
PyInstaller, and checks the finished app with `bundle_gpl_parts()` afterwards.

Both downloads are pinned by SHA-256. BtbN keeps its month-end builds; a daily build disappears after a few weeks,
so pin a month-end one. To move to another FFmpeg or PyAV release, change the constants below: PyAV's FFmpeg major
version must match (PyAV 18 is built for FFmpeg 8.1). Building needs the MSVC build tools (the GitHub Windows runners
have them) and takes about a minute. Downloads and the wheel are kept in `build/lgpl-av/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build" / "lgpl-av"

AV_VERSION = "18.1.0"
AV_SDIST = (
    "https://files.pythonhosted.org/packages/8d/f4/f22114d30d3435e38c6af2b4870f37b864403dca6ae7af747a289ce0a18e/"
    f"av-{AV_VERSION}.tar.gz",
    "47bfc286e1bc9de7ab4681fc2b575cd2460a66919d31ffe1bd5aa54fae531a28",
)
FFMPEG_BUILD = "ffmpeg-n8.1.3-27-g8dca1586f4-win64-lgpl-shared-8.1"
FFMPEG_ZIP = (
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/{FFMPEG_BUILD}.zip",
    "5cef7bc48e513f3cee7c881e8b2999d835541edb1846c3e89443a67f8f8556ab",
)
# Where the source of the bundled FFmpeg and of the libraries linked into it can be had (the notices name it).
FFMPEG_SOURCE = (
    "FFmpeg n8.1.3-27-g8dca1586f4: https://git.ffmpeg.org/ffmpeg.git (tag n8.1.3, commit 8dca1586f4); "
    "built by https://github.com/BtbN/FFmpeg-Builds (release autobuild-2026-10-31-12-59, variant win64-lgpl-shared), "
    "whose scripts.d folder names the source of every library linked in"
)
DELVEWHEEL = "delvewheel==1.13.2"

# FFmpeg's own list of GPL-only and non-free external libraries (configure: EXTERNAL_LIBRARY_GPL_LIST and
# EXTERNAL_LIBRARY_NONFREE_LIST), plus the switches that turn the whole build GPL or non-free. libpostproc is GPL too.
GPL_SWITCHES = (
    "gpl", "nonfree", "postproc",
    "avisynth", "frei0r", "libcdio", "libdavs2", "libdvdnav", "libdvdread", "librubberband", "libvidstab",
    "libx264", "libx265", "libxavs", "libxavs2", "libxvid",
    "decklink", "libfdk-aac", "libtls",
)  # fmt: skip
_ENABLED_GPL = re.compile(
    rb"--enable-(" + b"|".join(re.escape(s.encode()) for s in GPL_SWITCHES) + rb")(?=[\s\x00'\"])"
)
# DLLs that are GPL whatever FFmpeg's configure line says (a wheel can bundle them next to an "LGPL" FFmpeg).
GPL_DLL_PREFIXES = ("libx264", "libx265", "libxvid", "postproc", "librubberband", "libvidstab", "frei0r")
FFMPEG_DLL_PREFIXES = ("avcodec", "avformat", "avutil", "avfilter", "avdevice", "swscale", "swresample", "postproc")

_PROBE = (
    "import json, av, av._core as c; print(json.dumps({'version': av.__version__, 'libraries': "
    "{k: {'license': v['license'], 'configuration': v['configuration']} for k, v in c.library_meta.items()}}))"
)


class GplError(RuntimeError):
    """PyAV or the built app contains GPL parts."""


def gpl_parts(libraries: dict[str, dict[str, str]]) -> list[str]:
    """What makes an FFmpeg GPL or non-free, from PyAV's `library_meta` (name -> licence and configure line)."""
    found = []
    for name, info in sorted(libraries.items()):
        if "LGPL" not in info["license"]:
            found.append(f"{name} reports the licence '{info['license']}'")
        switches = sorted({m.decode() for m in _ENABLED_GPL.findall(info["configuration"].encode() + b" ")})
        if switches:
            found.append(f"{name} is built with " + ", ".join(f"--enable-{s}" for s in switches))
    return found


def installed_av(python: str = sys.executable) -> dict | None:
    """Version and FFmpeg libraries of the PyAV in `python`'s environment, or None when it is not installed.

    Asked in a child process: importing PyAV here would load its DLLs, and Windows cannot replace a loaded DLL."""
    result = subprocess.run([python, "-c", _PROBE], capture_output=True, text=True)
    if result.returncode != 0:
        if "No module named 'av'" in result.stderr:
            return None
        raise RuntimeError(f"PyAV does not import:\n{result.stderr.strip()}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def bundle_gpl_parts(app: Path) -> list[str]:
    """GPL libraries in a built app folder: GPL DLLs by name, and FFmpeg DLLs whose embedded configure line enables
    a GPL or non-free part (every FFmpeg library carries that line as a string)."""
    found = []
    for dll in sorted(app.rglob("*.dll")):
        name = dll.name.lower()
        if name.startswith(GPL_DLL_PREFIXES):
            found.append(f"{dll.relative_to(app)} is a GPL library")
        elif name.startswith(FFMPEG_DLL_PREFIXES):
            switches = sorted({m.decode() for m in _ENABLED_GPL.findall(dll.read_bytes())})
            if switches:
                found.append(f"{dll.relative_to(app)} is built with " + ", ".join(f"--enable-{s}" for s in switches))
    return found


def _download(url: str, sha256: str) -> Path:
    target = WORK / url.rsplit("/", 1)[1]
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
        return target
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "nexusvox-build"})
    with urllib.request.urlopen(request, timeout=300) as response, open(target, "wb") as out:
        shutil.copyfileobj(response, out)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != sha256:
        target.unlink()
        raise GplError(f"{target.name} has SHA-256 {digest}, expected {sha256}")
    return target


def build_wheel() -> Path:
    """Build the LGPL PyAV wheel (cached in build/lgpl-av/) and return its path."""
    wheel_dir = WORK / "wheel"
    existing = sorted(wheel_dir.glob(f"av-{AV_VERSION}-*.whl"))
    if existing:
        return existing[0]

    ffmpeg_zip, sdist = _download(*FFMPEG_ZIP), _download(*AV_SDIST)
    for folder in (WORK / FFMPEG_BUILD, WORK / f"av-{AV_VERSION}", WORK / "raw"):
        shutil.rmtree(folder, ignore_errors=True)
    with zipfile.ZipFile(ffmpeg_zip) as archive:
        archive.extractall(WORK)
    with tarfile.open(sdist) as archive:
        archive.extractall(WORK, filter="data")
    ffmpeg, source = WORK / FFMPEG_BUILD, WORK / f"av-{AV_VERSION}"
    # setuptools puts files named COPYING* into the wheel's licences folder, so FFmpeg's LGPL text travels with
    # the DLLs and reaches the third-party notices with the rest of PyAV's licence files.
    shutil.copyfile(ffmpeg / "LICENSE.txt", source / "COPYING.FFmpeg-LGPL-3.0.txt")

    # On Windows PyAV's setup.py takes FFmpeg's headers and import libraries from the compiler's INCLUDE and LIB.
    env = dict(os.environ)
    env["INCLUDE"] = os.pathsep.join(filter(None, [str(ffmpeg / "include"), env.get("INCLUDE")]))
    env["LIB"] = os.pathsep.join(filter(None, [str(ffmpeg / "lib"), env.get("LIB")]))
    pip = [sys.executable, "-m", "pip"]
    subprocess.run([*pip, "wheel", "--no-deps", "-w", str(WORK / "raw"), str(source)], env=env, check=True)
    subprocess.run([*pip, "install", DELVEWHEEL], check=True)
    raw = next((WORK / "raw").glob("av-*.whl"))
    # Copies the FFmpeg DLLs into av.libs with unique names and makes `import av` find them, like the PyPI wheel.
    repair = [sys.executable, "-m", "delvewheel", "repair", "--add-path", str(ffmpeg / "bin"), "-w", str(wheel_dir)]
    subprocess.run([*repair, str(raw)], check=True)
    return next(wheel_dir.glob(f"av-{AV_VERSION}-*.whl"))


def ensure_lgpl_av() -> None:
    """Replace the installed PyAV with the LGPL build unless it already is one; raise GplError if that fails."""
    current = installed_av()
    if current and current["version"] == AV_VERSION and not gpl_parts(current["libraries"]):
        print(f"PyAV {AV_VERSION} without GPL parts is installed.")
        return
    why = "not installed" if current is None else "; ".join(gpl_parts(current["libraries"])) or current["version"]
    print(f"Replacing the installed PyAV ({why}) with PyAV {AV_VERSION} on an LGPL FFmpeg.")
    wheel = build_wheel()
    subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)], check=True)
    after = installed_av()
    problems = gpl_parts(after["libraries"]) if after else ["PyAV is not installed"]
    if problems:
        raise GplError("The installed PyAV still has GPL parts: " + "; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report whether the installed PyAV has GPL parts")
    args = parser.parse_args()
    try:
        if args.check:
            current = installed_av()
            problems = ["PyAV is not installed"] if current is None else gpl_parts(current["libraries"])
            for problem in problems:
                print(problem)
            return 1 if problems else 0
        ensure_lgpl_av()
    except (GplError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
