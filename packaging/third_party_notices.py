"""Write the third-party notices that the Windows installer shows and installs.

    python packaging/third_party_notices.py [output.txt]     # default: dist/installer/THIRD_PARTY_NOTICES.txt

The installer redistributes NexusVox's runtime dependencies (the frozen app contains them), and MIT, BSD, Apache and
LGPL all require their copyright and licence notices to travel with a binary copy. `THIRD_PARTY_LICENSES.md` is the
source-tree record; this file is what the *installer* ships. It is generated from the environment the app was built
in: every package that NexusVox requires, directly or not, with its licence files as the wheel carries them.

Run it with the environment that has NexusVox installed (`pip install .`, as for `packaging/build.py`). The output
is plain text with a byte-order mark and Windows line ends, because Inno Setup shows it as it is.
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dist" / "installer" / "THIRD_PARTY_NOTICES.txt"

# Licence texts in a wheel: LICENSE, LICENCE, COPYING, NOTICE and AUTHORS files, wherever the build backend put them.
_LICENCE_FILE = re.compile(r"(licen[sc]e|copying|notice|authors)", re.IGNORECASE)

BUNDLED_NOTE = """\
Native libraries inside the packages above
------------------------------------------
Some of the packages carry compiled libraries of their own. PyAV ("av") contains FFmpeg libraries (in av.libs),
sounddevice contains PortAudio, and onnxruntime, ctranslate2 and numpy contain their own native code. Their licence
texts are the ones in the package entries above. For FFmpeg, the source code that matches the shipped libraries is
available from https://ffmpeg.org and https://github.com/PyAV-Org/pyav-ffmpeg.

Speech models
-------------
No model is installed with NexusVox. A model is downloaded from Hugging Face when you first dictate and is covered by
its own licence (for Parakeet TDT 0.6B v3: CC-BY-4.0, (c) NVIDIA). See THIRD_PARTY_LICENSES.md.
"""


def runtime_distributions(root: str = "nexusvox") -> list[metadata.Distribution]:
    """`root` and everything it requires at run time, found by walking the installed metadata."""
    seen: dict[str, metadata.Distribution] = {}
    queue = [root]
    while queue:
        name = queue.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # a requirement whose marker excludes this platform or Python version
        seen[key] = dist
        for requirement in dist.requires or []:
            parsed = Requirement(requirement)
            if parsed.marker is None or parsed.marker.evaluate({"extra": ""}):
                queue.append(parsed.name)
    seen.pop(re.sub(r"[-_.]+", "-", root).lower())
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


def _is_licence_file(file: metadata.PackagePath) -> bool:
    """A licence text in the wheel: in the .dist-info folder, or at most one folder deep in the package."""
    if file.suffix.lower() not in {"", ".txt", ".md", ".rst"} or not _LICENCE_FILE.search(file.name):
        return False
    parts = file.parts
    return any(part.endswith(".dist-info") for part in parts) or (len(parts) <= 2 and parts[0] != "tests")


def licence_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    found = []
    for file in dist.files or []:
        if _is_licence_file(file):
            path = Path(str(dist.locate_file(file)))
            if path.is_file():
                found.append((file.name, path.read_text(encoding="utf-8", errors="replace").strip()))
    return found


def build_notices() -> str:
    own_licences = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    # The source-tree file ends the bundled-assets part at the first horizontal rule after its heading.
    geist = (ROOT / "src" / "nexusvox" / "dashboard" / "static" / "Geist-OFL.txt").read_text(encoding="utf-8")
    bundled_assets = own_licences.split("## Bundled third-party software", 1)[1].split("\n---\n", 1)[0].strip()
    parts = [
        "Third-party notices for the NexusVox Windows installer\n"
        "=======================================================\n\n"
        "NexusVox\nCopyright (C) 2026 NightShift AI GmbH\n\n"
        "NexusVox itself is source-available under the PolyForm Noncommercial License 1.0.0 (see LICENSE). It is not\n"
        "open source. The installer also contains the third-party software listed below, which is licensed to you\n"
        "under its own terms, reproduced here as those terms require. Those licences apply to that software only.\n",
        "Bundled in the dashboard\n------------------------\n" + bundled_assets.replace("```", ""),
        "--- Geist-OFL.txt ---\n" + geist.strip(),
        "Python packages bundled in the app\n----------------------------------\n",
    ]
    for dist in runtime_distributions():
        name, version = dist.metadata["Name"], dist.version
        entry = [f"### {name} {version}", f"Licence: {licence_summary(dist)}"]
        home = dist.metadata.get("Home-page") or next(
            (u.split(",", 1)[-1].strip() for u in dist.metadata.get_all("Project-URL") or []), ""
        )
        if home:
            entry.append(f"Homepage: {home}")
        for filename, text in licence_texts(dist):
            entry.append(f"\n--- {filename} ---\n{text}")
        parts.append("\n".join(entry))
    parts.append(BUNDLED_NOTE)
    return "\n\n".join(parts) + "\n"


def write_notices(output: Path = DEFAULT_OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = build_notices().replace("\r\n", "\n").replace("\n", "\r\n")
    output.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    return output


def main() -> int:
    output = write_notices(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT)
    print(f"Notices: {output} ({output.stat().st_size / 1000:.0f} kB, {len(runtime_distributions())} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
