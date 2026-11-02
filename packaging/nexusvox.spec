# PyInstaller spec for NexusVox: one-folder (onedir), windowed. Never one-file: see docs/installer-research.md.
#
# Build with:  python packaging/build.py
# Run from the repository root; the spec resolves everything relative to its own folder.
#
# The CPU speech path is bundled (onnx-asr/onnxruntime for Parakeet, faster-whisper/ctranslate2 for Whisper).
# No model is bundled: the models are downloaded on first use.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

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
    try:
        d, b, h = collect_all(package)
    except Exception as exc:  # a package missing from the build environment is reported, not fatal
        print(f"WARNING: collect_all({package!r}) failed: {exc}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

# `_sounddevice_data` carries the PortAudio DLL.
datas += collect_data_files("_sounddevice_data")

# Extensions (the Pro add-on) are found through importlib.metadata entry points, which read the
# .dist-info folders. Keep nexusvox's own so `entry_points(group="nexusvox.extensions")` works.
datas += copy_metadata("nexusvox")

# Dropped to save space: test suites and docs of the bundled packages are never read at run time.
excludes = ["pytest", "IPython", "matplotlib", "scipy", "pandas"]

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
    upx=False,  # UPX-packed binaries raise antivirus flags
)
coll = COLLECT(exe, a.binaries, a.datas, name="NexusVox", upx=False)
