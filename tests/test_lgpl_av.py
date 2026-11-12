"""The installer ships no GPL part of FFmpeg: packaging/lgpl_av.py and the GPL checks of the notices."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def _load(name: str):
    """Import a script from packaging/ by path, as tests/test_installer_packaging.py does."""
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(PACKAGING))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PACKAGING))
    return module


lgpl_av = _load("lgpl_av")
notices = _load("third_party_notices")

# Shortened from the real configure lines: the PyPI wheel says LGPL but links x264 and x265; BtbN's LGPL build
# names them only as disabled.
PYPI_CONFIGURE = "--enable-shared --enable-version3 --enable-libdav1d --enable-libx264 --enable-libx265 --enable-nvenc"
LGPL_CONFIGURE = (
    "--enable-version3 --enable-shared --enable-libdav1d --enable-libvorbis --enable-libmp3lame --enable-libopus "
    "--disable-libx264 --disable-libx265 --disable-libxvid --enable-schannel --enable-libzimg"
)


def _meta(configuration: str, licence: str = "LGPL version 3 or later") -> dict:
    return {name: {"license": licence, "configuration": configuration} for name in ("libavcodec", "libavutil")}


def test_the_pypi_ffmpeg_is_flagged_although_it_reports_lgpl():
    found = lgpl_av.gpl_parts(_meta(PYPI_CONFIGURE))
    assert found == [
        "libavcodec is built with --enable-libx264, --enable-libx265",
        "libavutil is built with --enable-libx264, --enable-libx265",
    ]


def test_the_lgpl_build_passes():
    assert lgpl_av.gpl_parts(_meta(LGPL_CONFIGURE)) == []


@pytest.mark.parametrize("switch", ["--enable-gpl", "--enable-nonfree", "--enable-libxavs2", "--enable-libfdk-aac"])
def test_a_gpl_or_nonfree_switch_at_the_end_of_the_line_is_flagged(switch):
    assert lgpl_av.gpl_parts(_meta(f"--enable-version3 {switch}"))


def test_a_gpl_licence_string_is_flagged():
    found = lgpl_av.gpl_parts(_meta("--enable-gpl", licence="GPL version 2 or later"))
    assert "libavcodec reports the licence 'GPL version 2 or later'" in found


def _app(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for relative, content in files.items():
        path = tmp_path / "_internal" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return tmp_path


def test_a_bundle_with_the_pypi_dlls_is_refused(tmp_path):
    app = _app(
        tmp_path,
        {
            "av.libs/avcodec-62-abc.dll": b"MZ\x00" + PYPI_CONFIGURE.encode() + b"\x00LGPL version 3 or later\x00",
            "av.libs/libx264-165-abc.dll": b"MZ",
            "python312.dll": b"MZ",
        },
    )
    found = lgpl_av.bundle_gpl_parts(app)
    assert any("libx264-165-abc.dll is a GPL library" in line for line in found)
    assert any("avcodec-62-abc.dll is built with --enable-libx264, --enable-libx265" in line for line in found)
    assert not any("python312" in line for line in found)


def test_a_bundle_with_the_lgpl_dlls_passes(tmp_path):
    app = _app(
        tmp_path,
        {
            "av.libs/avcodec-62-def.dll": b"MZ\x00" + LGPL_CONFIGURE.encode() + b"\x00",
            "av.libs/swresample-6-def.dll": b"MZ\x00" + LGPL_CONFIGURE.encode() + b"\x00",
            "_sounddevice_data/portaudio-binaries/libportaudio64bit.dll": b"MZ",
        },
    )
    assert lgpl_av.bundle_gpl_parts(app) == []


def test_the_notices_refuse_a_gpl_bundle_before_reading_anything_else(tmp_path):
    app = _app(tmp_path, {"av.libs/avformat-62-abc.dll": b"--enable-libx265\x00"})
    with pytest.raises(notices.GplLibraryError, match="avformat-62-abc.dll is built with --enable-libx265"):
        notices.build_notices(app)
    # build_installer stops on MissingLicenceError, so a GPL bundle stops the installer build too.
    assert issubclass(notices.GplLibraryError, notices.MissingLicenceError)


def test_the_ffmpeg_section_names_the_source_and_the_linked_libraries():
    av = {"version": lgpl_av.AV_VERSION, "libraries": _meta(LGPL_CONFIGURE)}
    section = notices.ffmpeg_section(av)
    assert "report the licence 'LGPL version 3 or later'" in section
    assert lgpl_av.FFMPEG_SOURCE in section
    assert "    libdav1d - BSD-2-Clause" in section
    assert "    libmp3lame - LGPL-2.0-or-later" in section
    # Build options and disabled libraries are not listed as linked in.
    for absent in ("version3", "shared", "schannel", "libx264"):
        assert f"    {absent} -" not in section


def test_the_ffmpeg_section_of_another_pyav_does_not_claim_the_pinned_source():
    section = notices.ffmpeg_section({"version": "0.1", "libraries": _meta("--enable-libnewthing")})
    assert lgpl_av.FFMPEG_SOURCE not in section
    assert "    libnewthing - see its project" in section
    assert "not installed" in notices.ffmpeg_section(None)


def test_an_lgpl_pyav_of_the_pinned_version_is_not_rebuilt(monkeypatch):
    monkeypatch.setattr(
        lgpl_av, "installed_av", lambda: {"version": lgpl_av.AV_VERSION, "libraries": _meta(LGPL_CONFIGURE)}
    )
    monkeypatch.setattr(lgpl_av, "build_wheel", lambda: pytest.fail("must not build"))
    lgpl_av.ensure_lgpl_av()


def test_a_wheel_that_stays_gpl_after_the_install_is_an_error(monkeypatch, tmp_path):
    states = iter([None, {"version": lgpl_av.AV_VERSION, "libraries": _meta(PYPI_CONFIGURE)}])
    monkeypatch.setattr(lgpl_av, "installed_av", lambda: next(states))
    monkeypatch.setattr(lgpl_av, "build_wheel", lambda: tmp_path / "av.whl")
    monkeypatch.setattr(lgpl_av.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(lgpl_av.GplError, match="still has GPL parts"):
        lgpl_av.ensure_lgpl_av()
