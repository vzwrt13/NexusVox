# PyInstaller spec for NexusVox: one-folder (onedir), windowed. Never one-file: see docs/installer-research.md.
#
# Build with:  python packaging/build.py
# Run from the repository root; the spec resolves everything relative to its own folder.
#
# The CPU speech path is bundled (onnx-asr/onnxruntime for Parakeet, faster-whisper/ctranslate2 for Whisper).
# No model is bundled: the models are downloaded on first use.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

HERE = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller

datas, binaries, hiddenimports = [], [], []

# Packages with native libraries, data files or lazy imports that the static analysis misses.
for package in (
    "nexusvox",  # dashboard/static and config.example.toml
    "onnx_asr",
    "onnxruntime",
    "faster_whisper",  # ships the Silero VAD model
    "ctranslate2",
    "av",  # PyAV decodes audio in faster-whisper; its Cython submodules are imported lazily
    "lingua",
    "pycaw",
    "comtypes",
    "pyaudiowpatch",
    "sounddevice",
    "pystray",
    "pynput",
):
    d, b, h = collect_all(package)  # no try/except: a missing package must fail the build, not ship without it
    datas += d
    binaries += b
    hiddenimports += h

# `_sounddevice_data` carries the PortAudio DLL.
datas += collect_data_files("_sounddevice_data")

# Note: collect_all("nexusvox") above also copies nexusvox's own .dist-info, which importlib.metadata needs.

# Kept out of the bundle even if the build environment happens to have them (none is a dependency of NexusVox):
#  - pytest: test runner, development only
#  - IPython, matplotlib, scipy, pandas: heavy, never imported by the app. If a future feature imports one,
#    the build fails its self-test on that import instead of the exclusion hiding it, so remove it from here.
excludes = ["pytest", "IPython", "matplotlib", "scipy", "pandas"]

# The self-test (NexusVox.exe --selftest) downloads ~640 MiB and binds the dashboard port, so it is only
# built in on request (`python packaging/build.py --selftest`, which writes to dist/selftest) and never ships.
if not os.environ.get("NEXUSVOX_BUILD_SELFTEST"):
    excludes.append("frozen_selftest")

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(HERE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # one-folder: the binaries go next to the exe, not inside it
    name="NexusVox",
    console=False,
    icon=str(HERE / "nexusvox.ico"),  # made by make_icon.py from the logo kit
    upx=False,  # UPX-packed binaries raise antivirus flags
)
coll = COLLECT(exe, a.binaries, a.datas, name="NexusVox", upx=False)
