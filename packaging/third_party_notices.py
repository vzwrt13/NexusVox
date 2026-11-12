"""Write the third-party notices that the Windows installer shows and installs.

    python packaging/third_party_notices.py [output.txt]     # default: dist/installer/THIRD_PARTY_NOTICES.txt

The installer redistributes NexusVox's runtime dependencies (the frozen app contains them), and MIT, BSD, Apache and
LGPL all require their copyright and licence notices to travel with a binary copy. `THIRD_PARTY_LICENSES.md` is the
source-tree record; this file is what the *installer* ships. It is generated from the environment the app was built in:

- every package that NexusVox requires, directly or not, plus PyInstaller (its bootloader is in the app), each with the
  licence files its wheel carries; a package whose wheel carries none needs a text in `packaging/licences/<name>.txt`,
  or the build fails;
- the Python runtime's licence;
- the native libraries found in the built app folder (FFmpeg and its codecs inside PyAV, PortAudio, ...), named with
  the licence they are known under. A GPL library in that list is called out with where its source can be had.

Run it with the environment that has NexusVox installed (`pip install .`, as for `packaging/build.py`), after the
build. The output is plain text with a byte-order mark and Windows line ends, because Inno Setup shows it as it is.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dist" / "installer" / "THIRD_PARTY_NOTICES.txt"
BUNDLE_DIR = ROOT / "dist" / "NexusVox"
OVERRIDES_DIR = Path(__file__).resolve().parent / "licences"

# PyInstaller's bootloader (GPL-2.0 with an exception for the programs it builds) is inside every frozen app.
EXTRA_DISTRIBUTIONS = ("pyinstaller",)

# Licence texts in a wheel: LICENSE, LICENCE, COPYING, NOTICE and AUTHORS files, with any extension (COPYING.LGPL,
# LICENSE.APACHE), wherever the build backend put them. Everything under `.dist-info/licenses/` counts as well.
_LICENCE_FILE = re.compile(r"(licen[sc]e|copying|notice|authors)", re.IGNORECASE)
_NOT_TEXT = {".py", ".pyc", ".pyd", ".dll", ".so", ".exe", ".h", ".c", ".cpp", ".json", ".toml", ".yaml", ".yml"}

# Native libraries: file-name prefix (lower case) -> (component, licence). Matched by name, not verified against the
# binary; the file lists the libraries that are really in the build and says so when it cannot name one.
_FFMPEG = (
    "FFmpeg",
    "LGPL-3.0-or-later (built with --enable-version3); GPL when built with GPL parts, see https://ffmpeg.org/legal.html",
)
KNOWN_BINARIES: dict[str, tuple[str, str]] = {
    "avcodec": _FFMPEG,
    "avformat": _FFMPEG,
    "avutil": _FFMPEG,
    "avfilter": _FFMPEG,
    "avdevice": _FFMPEG,
    "swscale": _FFMPEG,
    "swresample": _FFMPEG,
    "libx264": ("x264", "GPL-2.0-or-later"),
    "libx265": ("x265", "GPL-2.0-or-later"),
    "libmp3lame": ("LAME", "LGPL-2.0-or-later"),
    "libopus": ("Opus", "BSD-3-Clause"),
    "libvpx": ("libvpx", "BSD-3-Clause"),
    "libdav1d": ("dav1d", "BSD-2-Clause"),
    "libsvtav1": ("SVT-AV1", "BSD-3-Clause-Clear and the AOM patent licence"),
    "libopencore": ("OpenCORE AMR", "Apache-2.0"),
    "libwebp": ("libwebp", "BSD-3-Clause"),
    "libsharpyuv": ("libwebp (sharpyuv)", "BSD-3-Clause"),
    "libvmaf": ("libvmaf", "BSD-2-Clause-Patent"),
    "libvpl": ("oneVPL dispatcher", "MIT"),
    "libiconv": ("GNU libiconv", "LGPL-2.1-or-later"),
    "zlib": ("zlib", "zlib licence"),
    "libgcc_s": ("GCC runtime", "GPL-3.0-or-later with the GCC Runtime Library Exception"),
    "libstdc++": ("GCC runtime", "GPL-3.0-or-later with the GCC Runtime Library Exception"),
    "libwinpthread": ("winpthreads (MinGW-w64)", "MIT-style (ZPL-2.1 for parts)"),
    "portaudio": ("PortAudio", "MIT-style, see the sounddevice entry"),
    "libssl": ("OpenSSL", "Apache-2.0"),
    "libcrypto": ("OpenSSL", "Apache-2.0"),
    "libffi": ("libffi", "MIT"),
    "sqlite3": ("SQLite", "public domain"),
    "python3": ("Python runtime", "PSF-2.0, see above"),
    "vcruntime": ("Microsoft Visual C++ Redistributable", "Microsoft licence terms for the redistributable"),
    "msvcp": ("Microsoft Visual C++ Redistributable", "Microsoft licence terms for the redistributable"),
    "concrt": ("Microsoft Visual C++ Redistributable", "Microsoft licence terms for the redistributable"),
    "ucrtbase": ("Microsoft Universal C Runtime", "Microsoft licence terms for the redistributable"),
    "libiomp5md": ("Intel OpenMP runtime", "Intel's licence for the runtime, see the package that ships it"),
    "cudnn": ("NVIDIA cuDNN", "NVIDIA proprietary licence"),
    "cublas": ("NVIDIA cuBLAS", "NVIDIA proprietary licence"),
    "cudart": ("NVIDIA CUDA runtime", "NVIDIA proprietary licence"),
}

GPL_SOURCES = """\
GPL libraries in this build
---------------------------
This build contains x264 and/or x265 (video encoders inside PyAV's FFmpeg libraries). They are licensed under the
GNU General Public License, version 2 or later (https://www.gnu.org/licenses/old-licenses/gpl-2.0.html). NexusVox does
not use the encoders: it uses PyAV, through faster-whisper, only to decode audio. Their source code is available from
    x264:  https://code.videolan.org/videolan/x264
    x265:  https://bitbucket.org/multicoreware/x265_git
and the build recipe for the exact libraries in this PyAV wheel from https://github.com/PyAV-Org/pyav-ffmpeg.
"""

FOOTER = """\
Speech models
-------------
No model is installed with NexusVox. A model is downloaded from Hugging Face when you first dictate and is covered by
its own licence (for Parakeet TDT 0.6B v3: CC-BY-4.0, (c) NVIDIA). See THIRD_PARTY_LICENSES.md.
"""


class MissingLicenceError(RuntimeError):
    """One or more bundled packages have no licence text; the notices would be incomplete."""


def runtime_distributions(
    root: str = "nexusvox",
    lookup: Callable[[str], metadata.Distribution] = metadata.distribution,
) -> list[metadata.Distribution]:
    """What `root` requires at run time, found by walking the installed metadata. `root` itself is not included.

    A requirement that applies here but is not installed is an error: the notices would silently leave it out.
    """
    root_key = canonicalize_name(root)
    seen: dict[str, metadata.Distribution] = {}
    queue = [root]
    while queue:
        name = queue.pop()
        key = canonicalize_name(name)
        if key in seen:
            continue
        try:
            seen[key] = lookup(name)
        except metadata.PackageNotFoundError:
            raise MissingLicenceError(f"{name} is required by {root} but not installed in this environment") from None
        for requirement in seen[key].requires or []:
            parsed = Requirement(requirement)
            if parsed.marker is None or parsed.marker.evaluate({"extra": ""}):
                queue.append(parsed.name)
    seen.pop(root_key)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def licence_summary(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.rsplit("::", 1)[-1].strip() for c in meta.get_all("Classifier") or [] if "License ::" in c]
    if classifiers:
        return "; ".join(classifiers)
    text = (meta.get("License") or "").strip()
    return text if text and "\n" not in text and len(text) < 100 else "see the licence text below"


def homepage(dist: metadata.Distribution) -> str:
    """The project's own page: Home-page, else the Project-URL labelled like a homepage or source, else nothing."""
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"]
    urls = [tuple(part.strip() for part in value.split(",", 1)) for value in meta.get_all("Project-URL") or []]
    for wanted in ("homepage", "home", "source", "repository", "github"):
        for label, url in (u for u in urls if len(u) == 2):
            if label.lower().startswith(wanted):
                return url
    return ""


def _is_licence_file(file: metadata.PackagePath) -> bool:
    """A licence text in the wheel: in the .dist-info folder, or at most one folder deep in the package."""
    if file.suffix.lower() in _NOT_TEXT:
        return False
    parts = file.parts
    if any(part.endswith(".dist-info") for part in parts):
        return "licenses" in parts[:-1] or bool(_LICENCE_FILE.search(file.name))
    return bool(_LICENCE_FILE.search(file.name)) and len(parts) <= 2 and parts[0] != "tests"


def licence_texts(dist: metadata.Distribution, overrides: Path = OVERRIDES_DIR) -> list[tuple[str, str]]:
    """The licence files of a distribution, else its checked-in override, else nothing."""
    found = []
    for file in dist.files or []:
        if _is_licence_file(file):
            path = Path(str(dist.locate_file(file)))
            if path.is_file():
                found.append((file.name, path.read_text(encoding="utf-8", errors="replace").strip()))
    override = overrides / f"{canonicalize_name(dist.metadata['Name'])}.txt"
    if not found and override.is_file():
        found.append((override.name, override.read_text(encoding="utf-8").strip()))
    return found


def identify_binary(file_name: str) -> tuple[str, str]:
    """(component, licence) for a native library by its file name; both say so when it is not known."""
    lowered = file_name.lower()
    for prefix, known in KNOWN_BINARIES.items():
        if lowered.startswith(prefix):
            return known
    return ("not identified", "see the Python package it came from")


def bundled_binaries(bundle: Path) -> list[Path]:
    """The DLLs next to the app's runtime and in the `*.libs` folders of wheels: what no pip metadata describes."""
    internal = bundle / "_internal"
    if not internal.is_dir():
        return []
    found = [p for p in internal.glob("*.dll")]
    for libs in internal.rglob("*.libs"):
        found += [p for p in libs.glob("*.dll")]
    return sorted(found, key=lambda p: p.name.lower())


def _binaries_section(bundle: Path) -> tuple[str, bool]:
    files = bundled_binaries(bundle)
    lines = ["Native libraries in the app", "---------------------------"]
    if not files:
        lines.append(f"(none found in {bundle})")
    gpl = False
    for path in files:
        component, licence = identify_binary(path.name)
        gpl = gpl or "GPL-2.0" in licence and "LGPL" not in licence
        lines.append(f"{path.name}\n    {component} - {licence}")
    lines.append(
        "\nLicence texts for the libraries inside Python packages (PyAV's FFmpeg, sounddevice's PortAudio,\n"
        "onnxruntime, ctranslate2, numpy) are in the package entries above where the package ships them."
    )
    return "\n".join(lines), gpl


def build_notices(bundle: Path = BUNDLE_DIR) -> str:
    own_licences = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    geist = (ROOT / "src" / "nexusvox" / "dashboard" / "static" / "Geist-OFL.txt").read_text(encoding="utf-8")
    # The source-tree file ends the bundled-assets part at the first horizontal rule after its heading.
    bundled_assets = own_licences.split("## Bundled third-party software", 1)[1].split("\n---\n", 1)[0].strip()
    python_licence = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_licence.is_file():
        raise MissingLicenceError(f"The Python licence file {python_licence} does not exist")

    parts = [
        "Third-party notices for the NexusVox Windows installer\n"
        "=======================================================\n\n"
        "NexusVox\nCopyright (C) 2026 NightShift AI GmbH\n\n"
        "NexusVox itself is source-available under the PolyForm Noncommercial License 1.0.0 (see LICENSE). It is not\n"
        "open source. The installer also contains the third-party software listed below, which is licensed to you\n"
        "under its own terms, reproduced here as those terms require. Those licences apply to that software only.\n",
        "Bundled in the dashboard\n------------------------\n" + bundled_assets.replace("```", ""),
        "--- Geist-OFL.txt ---\n" + geist.strip(),
        "Python runtime\n--------------\nThe app contains the Python interpreter and standard library.\n\n"
        "--- LICENSE.txt ---\n" + python_licence.read_text(encoding="utf-8", errors="replace").strip(),
        "Python packages bundled in the app\n----------------------------------\n",
    ]
    distributions = runtime_distributions()
    for extra in EXTRA_DISTRIBUTIONS:
        try:
            distributions.append(metadata.distribution(extra))
        except metadata.PackageNotFoundError:
            raise MissingLicenceError(f"{extra} is not installed; the bundle contains its bootloader") from None
    missing = []
    for dist in sorted(distributions, key=lambda d: d.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        entry = [f"### {name} {version}", f"Licence: {licence_summary(dist)}"]
        if homepage(dist):
            entry.append(f"Homepage: {homepage(dist)}")
        texts = licence_texts(dist)
        if not texts:
            missing.append(f"{name} {version}")
        for filename, text in texts:
            entry.append(f"\n--- {filename} ---\n{text}")
        parts.append("\n".join(entry))
    if missing:
        raise MissingLicenceError(
            "No licence text for: " + ", ".join(missing) + ". Add packaging/licences/<name>.txt for each "
            "(the text from the project's repository), or drop the package from the build."
        )

    binaries, gpl = _binaries_section(bundle)
    parts.append(binaries)
    if gpl:
        print("warning: the build contains GPL libraries (x264/x265); see the notices file", file=sys.stderr)
        parts.append(GPL_SOURCES)
    parts.append(FOOTER)
    return "\n\n".join(parts) + "\n"


def write_notices(output: Path = DEFAULT_OUTPUT, bundle: Path = BUNDLE_DIR) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = build_notices(bundle).replace("\r\n", "\n").replace("\n", "\r\n")
    output.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    return output


def main() -> int:
    try:
        output = write_notices(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT)
    except MissingLicenceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Notices: {output} ({output.stat().st_size / 1000:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
