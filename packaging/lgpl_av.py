"""Put a PyAV without GPL parts into the build environment, and check a built app for GPL parts.

    python packaging/lgpl_av.py            # replace the installed PyAV unless it already is the pinned LGPL build
    python packaging/lgpl_av.py --check    # only report: exit 1 when the installed PyAV is not that build

The PyAV wheels on PyPI bundle an FFmpeg built with x264 and x265, which are GPL; an installer that ships them falls
under the GPL (https://ffmpeg.org/legal.html), which NexusVox's source-available licence cannot follow. NexusVox
only decodes audio (uploads, and faster-whisper's own loader), so it needs none of them. This script builds the
same PyAV release from its source distribution against BtbN's LGPL build of FFmpeg (the `lgpl-shared` variant:
no `--enable-gpl`, no x264, x265, xvid or other GPL-only library), bundles the FFmpeg DLLs into the wheel with
delvewheel, as the PyPI wheel does, and installs it in place of the PyPI one. `packaging/build.py` runs it before
PyInstaller and checks the finished app with `bundle_gpl_parts()` afterwards; the third-party notices read the
bundled FFmpeg DLLs with `bundle_ffmpeg()`, and the frozen self-test checks the running FFmpeg with `gpl_parts()`.
This module is the one place that knows what counts as GPL.

Both downloads are pinned by SHA-256. BtbN keeps its month-end builds; a daily build disappears after a few weeks,
so pin a month-end one. To move to another FFmpeg or PyAV release, change the constants below (FFMPEG_SOURCE too):
PyAV's FFmpeg major version must match (PyAV 18 is built for FFmpeg 8.1). Building needs the MSVC build tools (the
GitHub Windows runners have them) and takes about a minute. Downloads and wheels are kept in `build/lgpl-av/`, a
wheel per FFmpeg build and Python version.
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
# FFmpeg's own version string, which every one of its DLLs carries (BtbN appends the build date to it).
FFMPEG_VERSION = "n8.1.3-27-g8dca1586f4"
FFMPEG_BUILD = f"ffmpeg-{FFMPEG_VERSION}-win64-lgpl-shared-8.1"
FFMPEG_ZIP = (
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/{FFMPEG_BUILD}.zip",
    "5cef7bc48e513f3cee7c881e8b2999d835541edb1846c3e89443a67f8f8556ab",
)
# Where the source of the pinned FFmpeg and of the libraries linked into it can be had (the notices name it).
FFMPEG_SOURCE = (
    f"FFmpeg {FFMPEG_VERSION}: https://git.ffmpeg.org/ffmpeg.git (tag n8.1.3, commit 8dca1586f4); "
    "built by https://github.com/BtbN/FFmpeg-Builds (release autobuild-2026-10-31-12-59, variant win64-lgpl-shared), "
    "whose scripts.d folder names the source of every library linked in"
)
# Put into the wheel next to PyAV's own licence, so FFmpeg's LGPL text travels with the DLLs into the app.
FFMPEG_LICENCE_FILE = "COPYING.FFmpeg-LGPL-3.0.txt"
DELVEWHEEL = "delvewheel==1.13.2"

# FFmpeg's own list of GPL-only and non-free external libraries (configure: EXTERNAL_LIBRARY_GPL_LIST and
# EXTERNAL_LIBRARY_NONFREE_LIST), plus the switches that turn the whole build GPL or non-free. libpostproc is GPL too.
GPL_SWITCHES = (
    "gpl", "nonfree", "postproc",
    "avisynth", "frei0r", "libcdio", "libdavs2", "libdvdnav", "libdvdread", "librubberband", "libvidstab",
    "libx264", "libx265", "libxavs", "libxavs2", "libxvid",
    "decklink", "libfdk-aac", "libtls",
)  # fmt: skip
_ENABLED_GPL = re.compile(r"--enable-(" + "|".join(re.escape(s) for s in GPL_SWITCHES) + r")(?=[\s'\"]|$)")
# DLLs that are GPL whatever FFmpeg's configure line says (a wheel can bundle them next to an "LGPL" FFmpeg).
# Every GPL-only library of GPL_SWITCHES that comes as a DLL of its own ("libxavs" covers libxavs2).
GPL_DLL_PREFIXES = (
    "libx264", "libx265", "libxvid", "libxavs", "libdavs2", "libcdio", "libdvdnav", "libdvdread",
    "postproc", "librubberband", "libvidstab", "frei0r",
)  # fmt: skip
FFMPEG_DLL_PREFIXES = ("avcodec", "avformat", "avutil", "avfilter", "avdevice", "swscale", "swresample")

# Strings every FFmpeg library carries: "libavcodec license: LGPL version 3 or later" (what avcodec_license()
# returns) and the configure line (avcodec_configuration()). avutil also carries the version (av_version_info()).
_LICENCE = re.compile(r"lib\w+ license: (.+)")

_PROBE = (
    "import json, av, av._core as c; print(json.dumps({'version': av.__version__, 'ffmpeg': av.ffmpeg_version_info, "
    "'libraries': {k: {'license': v['license'], 'configuration': v['configuration']} "
    "for k, v in c.library_meta.items()}}))"
)


class GplError(RuntimeError):
    """PyAV or the built app contains GPL parts."""


class LgplBuildError(RuntimeError):
    """The LGPL PyAV could not be built or installed (download, integrity, compiler, wheel contents)."""


def gpl_parts(libraries: dict[str, dict[str, str]]) -> list[str]:
    """What makes an FFmpeg GPL or non-free, from name -> {"license", "configuration"} (PyAV's `library_meta`, or
    what `bundle_ffmpeg` reads from the DLLs). A library that reports no LGPL licence counts, too."""
    found = []
    for name, info in sorted(libraries.items()):
        if "LGPL" not in info["license"]:
            found.append(f"{name} reports the licence '{info['license']}'")
        switches = sorted(set(_ENABLED_GPL.findall(info["configuration"])))
        if switches:
            found.append(f"{name} is built with " + ", ".join(f"--enable-{s}" for s in switches))
    return found


def is_pinned_ffmpeg(version: str) -> bool:
    """Whether an FFmpeg version string ("n8.1.3-27-g8dca1586f4-20261031") is the pinned BtbN build."""
    return version == FFMPEG_VERSION or version.startswith(FFMPEG_VERSION + "-")


def installed_av(python: str = sys.executable) -> dict | None:
    """Version, FFmpeg version and FFmpeg libraries of the PyAV in `python`'s environment; None when not installed.

    Asked in a child process: importing PyAV here would load its DLLs, and Windows cannot replace a loaded DLL."""
    result = subprocess.run([python, "-c", _PROBE], capture_output=True, text=True)
    if result.returncode != 0:
        if "No module named 'av'" in result.stderr:
            return None
        raise LgplBuildError(f"PyAV does not import:\n{result.stderr.strip()}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def why_not_pinned(av: dict | None) -> list[str]:
    """Why an installed PyAV (see installed_av) is not the pinned LGPL build; empty when it is."""
    if av is None:
        return ["PyAV is not installed"]
    problems = gpl_parts(av["libraries"])
    if av["version"] != AV_VERSION:
        problems.append(f"PyAV is {av['version']}, not {AV_VERSION}")
    if not is_pinned_ffmpeg(av["ffmpeg"]):
        problems.append(f"its FFmpeg is {av['ffmpeg']}, not the pinned {FFMPEG_VERSION}")
    return problems


def _strings_containing(data: bytes, needle: bytes) -> list[str]:
    """The runs of printable ASCII in `data` that contain `needle` (a C string and whatever printable bytes touch
    it). Found with bytes.find: a regex over a 90 MB DLL takes the better part of a minute."""
    runs, position = [], data.find(needle)
    while position != -1:
        start, end = position, position + len(needle)
        while start > 0 and 0x20 <= data[start - 1] <= 0x7E:
            start -= 1
        while end < len(data) and 0x20 <= data[end] <= 0x7E:
            end += 1
        runs.append(data[start:end].decode("ascii"))
        position = data.find(needle, end)
    return runs


def bundle_ffmpeg(app: Path) -> dict[str, dict[str, str]]:
    """The FFmpeg DLLs in a built app folder, read from the files themselves: path (relative to the app) ->
    {"license", "configuration"}. An entry with an empty licence is a DLL that does not say (gpl_parts flags it)."""
    found = {}
    for dll in sorted(app.rglob("*.dll")):
        if not dll.name.lower().startswith(FFMPEG_DLL_PREFIXES):
            continue
        data = dll.read_bytes()
        licences = [m.group(1) for s in _strings_containing(data, b" license: ") if (m := _LICENCE.search(s))]
        configurations = [s[s.find("--") :] for s in _strings_containing(data, b"--enable-")]
        found[str(dll.relative_to(app))] = {
            "license": licences[0] if licences else "",
            "configuration": max(configurations, key=len) if configurations else "",
        }
    return found


def bundle_is_pinned_ffmpeg(app: Path) -> bool:
    """Whether the app's FFmpeg is the pinned build: its avutil (where av_version_info() lives) carries a version
    string that is_pinned_ffmpeg accepts, by the same rule (a longer version with the same prefix does not count).
    False when the app has no avutil."""
    return any(
        is_pinned_ffmpeg(word)
        for dll in app.rglob("*.dll")
        if dll.name.lower().startswith("avutil")
        for text in _strings_containing(dll.read_bytes(), FFMPEG_VERSION.encode())
        for word in text.split()
    )


def bundle_gpl_parts(app: Path, ffmpeg: dict[str, dict[str, str]] | None = None) -> list[str]:
    """GPL libraries in a built app folder: GPL DLLs by name, and FFmpeg DLLs that report a non-LGPL licence or
    whose embedded configure line enables a GPL or non-free part. `ffmpeg` is what bundle_ffmpeg(app) returned,
    for a caller that has read the DLLs already (avcodec alone is about 90 MB)."""
    found = [
        f"{dll.relative_to(app)} is a GPL library"
        for dll in sorted(app.rglob("*.dll"))
        if dll.name.lower().startswith(GPL_DLL_PREFIXES)
    ]
    return found + gpl_parts(bundle_ffmpeg(app) if ffmpeg is None else ffmpeg)


def _sha256(path: Path) -> str:
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _download(url: str, sha256: str) -> Path:
    target = WORK / url.rsplit("/", 1)[1]
    if target.is_file() and _sha256(target) == sha256:
        return target
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "nexusvox-build"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response, open(target, "wb") as out:
            shutil.copyfileobj(response, out)
    except OSError as error:
        raise LgplBuildError(f"Downloading {url} failed: {error}") from error
    digest = _sha256(target)
    if digest != sha256:
        target.unlink()
        raise LgplBuildError(f"{target.name} is not the pinned file: SHA-256 {digest}, expected {sha256}")
    return target


def _extract_sdist(sdist: Path, into: Path) -> None:
    with tarfile.open(sdist) as archive:
        if hasattr(tarfile, "data_filter"):  # Python 3.11.4 and later
            archive.extractall(into, filter="data")
        else:  # the archive is pinned by SHA-256, so its member paths are known to be safe
            archive.extractall(into)  # noqa: S202


def wheel_folder() -> Path:
    """Where the wheel for this FFmpeg build and this Python version is kept: a wheel built for another Python
    (PyAV builds cp311-abi3 for 3.11 to 3.13 and a version-specific one for 3.14) or FFmpeg is never reused."""
    return WORK / "wheel" / f"{FFMPEG_VERSION}-av{AV_VERSION}-py{sys.version_info.major}{sys.version_info.minor}"


def _the_wheel(folder: Path, pattern: str, step: str) -> Path:
    """The one wheel `step` wrote into `folder`; LgplBuildError when it wrote none."""
    wheels = sorted(folder.glob(pattern))
    if not wheels:
        raise LgplBuildError(f"{step} produced no wheel matching {pattern} in {folder}")
    return wheels[0]


def wheel_problem(wheel: Path) -> str | None:
    """Why a built wheel cannot be shipped (not a readable wheel, or without FFmpeg's licence text); None when it
    can. Checked on every build, cached wheels included: a run that died before the check may have left one."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            damaged = archive.testzip()
    except (OSError, zipfile.BadZipFile) as error:
        return f"{wheel.name} is not a readable wheel: {error}"
    if damaged:
        return f"{wheel.name} is damaged ({damaged})"
    if not any(name.endswith(f".dist-info/licenses/{FFMPEG_LICENCE_FILE}") for name in names):
        return f"{wheel.name} does not carry {FFMPEG_LICENCE_FILE}; PyAV's licence files changed"
    return None


def build_wheel() -> Path:
    """Build the LGPL PyAV wheel (cached in wheel_folder()) and return its path."""
    wheel_dir = wheel_folder()
    existing = sorted(wheel_dir.glob(f"av-{AV_VERSION}-*.whl"))
    if existing:
        problem = wheel_problem(existing[0])
        if problem is None:
            return existing[0]
        print(f"Building the wheel again: the cached {problem}.")
        shutil.rmtree(wheel_dir)

    ffmpeg_zip, sdist = _download(*FFMPEG_ZIP), _download(*AV_SDIST)
    raw_dir = WORK / "raw"
    for folder in (WORK / FFMPEG_BUILD, WORK / f"av-{AV_VERSION}", raw_dir):
        shutil.rmtree(folder, ignore_errors=True)
    with zipfile.ZipFile(ffmpeg_zip) as archive:
        archive.extractall(WORK)  # pinned by SHA-256
    _extract_sdist(sdist, WORK)
    ffmpeg, source = WORK / FFMPEG_BUILD, WORK / f"av-{AV_VERSION}"
    # setuptools puts files named COPYING* into the wheel's licences folder (PyAV sets no license-files list);
    # checked below, because the notices take FFmpeg's licence text from there.
    shutil.copyfile(ffmpeg / "LICENSE.txt", source / FFMPEG_LICENCE_FILE)

    # On Windows PyAV's setup.py takes FFmpeg's headers and import libraries from the compiler's INCLUDE and LIB.
    env = dict(os.environ)
    env["INCLUDE"] = os.pathsep.join(filter(None, [str(ffmpeg / "include"), env.get("INCLUDE")]))
    env["LIB"] = os.pathsep.join(filter(None, [str(ffmpeg / "lib"), env.get("LIB")]))
    pip = [sys.executable, "-m", "pip"]
    subprocess.run([*pip, "wheel", "--no-deps", "-w", str(raw_dir), str(source)], env=env, check=True)
    subprocess.run([*pip, "install", DELVEWHEEL], check=True)
    raw = _the_wheel(raw_dir, "av-*.whl", "pip wheel")
    # Copies the FFmpeg DLLs into av.libs with unique names and makes `import av` find them, like the PyPI wheel.
    repair = [sys.executable, "-m", "delvewheel", "repair", "--add-path", str(ffmpeg / "bin"), "-w", str(wheel_dir)]
    subprocess.run([*repair, str(raw)], check=True)
    wheel = _the_wheel(wheel_dir, f"av-{AV_VERSION}-*.whl", "delvewheel repair")
    problem = wheel_problem(wheel)  # the wheel is closed again here, so Windows lets the folder go
    if problem:
        shutil.rmtree(wheel_dir)
        raise LgplBuildError(f"The built {problem}")
    return wheel


def ensure_lgpl_av() -> None:
    """Replace the installed PyAV with the pinned LGPL build unless it already is that build.

    Raises GplError when the result still has GPL parts, LgplBuildError when it cannot be built or installed."""
    problems = why_not_pinned(installed_av())
    if not problems:
        print(f"PyAV {AV_VERSION} on the pinned LGPL FFmpeg {FFMPEG_VERSION} is installed.")
        return
    print(f"Replacing the installed PyAV ({'; '.join(problems)}) with PyAV {AV_VERSION} on FFmpeg {FFMPEG_VERSION}.")
    wheel = build_wheel()
    subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)], check=True)
    problems = why_not_pinned(installed_av())
    if problems:
        raise GplError("The installed PyAV is still not the pinned LGPL build: " + "; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report whether the installed PyAV is that build")
    args = parser.parse_args()
    try:
        if args.check:
            problems = why_not_pinned(installed_av())
            for problem in problems:
                print(problem)
            return 1 if problems else 0
        ensure_lgpl_av()
    except (GplError, LgplBuildError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
