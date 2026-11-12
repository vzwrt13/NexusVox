"""The installer ships no GPL part of FFmpeg: packaging/lgpl_av.py and the GPL checks of the notices."""

from __future__ import annotations

import importlib.util
import io
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
    "--prefix=/ffbuild/prefix --enable-version3 --enable-shared --enable-libdav1d --enable-libvorbis "
    "--enable-libmp3lame --enable-libopus --disable-libx264 --disable-libx265 --disable-libxvid --enable-schannel"
)
LGPL = "LGPL version 3 or later"


def _meta(configuration: str, licence: str = LGPL) -> dict:
    return {name: {"license": licence, "configuration": configuration} for name in ("libavcodec", "libavutil")}


def test_the_pypi_ffmpeg_is_flagged_although_it_reports_lgpl():
    assert lgpl_av.gpl_parts(_meta(PYPI_CONFIGURE)) == [
        "libavcodec is built with --enable-libx264, --enable-libx265",
        "libavutil is built with --enable-libx264, --enable-libx265",
    ]


def test_the_lgpl_build_passes():
    assert lgpl_av.gpl_parts(_meta(LGPL_CONFIGURE)) == []


@pytest.mark.parametrize(
    "switch", ["--enable-gpl", "--enable-nonfree", "--enable-libxavs2", "--enable-libfdk-aac", "--enable-libxvid"]
)
def test_a_gpl_or_nonfree_switch_at_the_end_of_the_line_is_flagged(switch):
    assert lgpl_av.gpl_parts(_meta(f"--enable-version3 {switch}"))


def test_a_gpl_or_missing_licence_string_is_flagged():
    assert "libavcodec reports the licence 'GPL version 2 or later'" in lgpl_av.gpl_parts(
        _meta("", licence="GPL version 2 or later")
    )
    assert "libavcodec reports the licence ''" in lgpl_av.gpl_parts(_meta(LGPL_CONFIGURE, licence=""))


@pytest.mark.parametrize(
    ("version", "pinned"),
    [
        (f"{lgpl_av.FFMPEG_VERSION}-20261031", True),
        (lgpl_av.FFMPEG_VERSION, True),
        ("8.1.2", False),
        (f"{lgpl_av.FFMPEG_VERSION}5-20261031", False),
    ],
)
def test_only_the_pinned_ffmpeg_counts_as_pinned(version, pinned):
    assert lgpl_av.is_pinned_ffmpeg(version) is pinned


def _ffmpeg_dll(name: str, configuration: str, licence: str = LGPL, version: str = "8.1.2") -> bytes:
    """What an FFmpeg DLL carries: its licence string, its configure line and (avutil) its version, as C strings
    between binary bytes."""
    return (
        b"MZ\x90\x00\x03"
        + f"{name} license: {licence}".encode()
        + b"\x00\x01\xff"
        + configuration.encode()
        + b"\x00\x00\xfe"
        + version.encode()
        + b"\x00\x80"
    )


def _app(tmp_path: Path, files: dict[str, bytes | str]) -> Path:
    for relative, content in files.items():
        path = tmp_path / "_internal" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return tmp_path


def _pinned_app(tmp_path: Path, with_licence: bool = True) -> Path:
    version = f"{lgpl_av.FFMPEG_VERSION}-20261031"
    files: dict[str, bytes | str] = {
        "av.libs/avcodec-62-def.dll": _ffmpeg_dll("libavcodec", LGPL_CONFIGURE, version=version),
        "av.libs/avutil-60-def.dll": _ffmpeg_dll("libavutil", LGPL_CONFIGURE, version=version),
        "_sounddevice_data/portaudio-binaries/libportaudio64bit.dll": b"MZ",
    }
    if with_licence:
        files[f"av-18.1.0.dist-info/licenses/{lgpl_av.FFMPEG_LICENCE_FILE}"] = "GNU LESSER GENERAL PUBLIC LICENSE v3"
    return _app(tmp_path, files)


def test_a_bundle_with_the_pypi_dlls_is_refused(tmp_path):
    app = _app(
        tmp_path,
        {
            "av.libs/avcodec-62-abc.dll": _ffmpeg_dll("libavcodec", PYPI_CONFIGURE),
            "av.libs/libx264-165-abc.dll": b"MZ",
            "python312.dll": b"MZ",
        },
    )
    found = lgpl_av.bundle_gpl_parts(app)
    assert any("libx264-165-abc.dll is a GPL library" in line for line in found)
    assert any("avcodec-62-abc.dll is built with --enable-libx264, --enable-libx265" in line for line in found)
    assert not any("python312" in line for line in found)
    assert lgpl_av.bundle_is_pinned_ffmpeg(app) is False


def test_a_bundle_with_the_pinned_dlls_passes_and_is_read_from_the_files(tmp_path):
    app = _pinned_app(tmp_path)
    assert lgpl_av.bundle_gpl_parts(app) == []
    assert lgpl_av.bundle_is_pinned_ffmpeg(app) is True
    read = lgpl_av.bundle_ffmpeg(app)
    assert read[str(Path("_internal/av.libs/avcodec-62-def.dll"))] == {"license": LGPL, "configuration": LGPL_CONFIGURE}


def test_an_ffmpeg_dll_without_a_licence_string_is_refused(tmp_path):
    app = _app(tmp_path, {"av.libs/swscale-9-abc.dll": b"MZ\x00" + LGPL_CONFIGURE.encode() + b"\x00"})
    assert lgpl_av.bundle_gpl_parts(app) == [f"{Path('_internal/av.libs/swscale-9-abc.dll')} reports the licence ''"]


def test_the_notices_refuse_a_gpl_bundle_before_reading_anything_else(tmp_path):
    app = _app(tmp_path, {"av.libs/avformat-62-abc.dll": _ffmpeg_dll("libavformat", "--enable-libx265")})
    with pytest.raises(notices.GplLibraryError, match="avformat-62-abc.dll is built with --enable-libx265"):
        notices.build_notices(app)
    # build_installer stops on MissingLicenceError, so a GPL bundle stops the installer build too.
    assert issubclass(notices.GplLibraryError, notices.MissingLicenceError)


def test_the_binaries_section_uses_the_same_gpl_detector(tmp_path):
    app = _app(tmp_path, {"av.libs/avcodec-62-abc.dll": _ffmpeg_dll("libavcodec", PYPI_CONFIGURE)})
    assert notices._binaries_section(app)[1] is True
    assert notices._binaries_section(_pinned_app(tmp_path / "pinned"))[1] is False


def test_the_ffmpeg_section_comes_from_the_bundle(tmp_path):
    section = notices.ffmpeg_section(_pinned_app(tmp_path))
    assert f"report the licence '{LGPL}'" in section
    assert lgpl_av.FFMPEG_SOURCE in section
    assert "    libdav1d - BSD-2-Clause" in section
    assert "    libmp3lame - LGPL-2.0-or-later" in section
    assert f"--- {lgpl_av.FFMPEG_LICENCE_FILE} ---\nGNU LESSER GENERAL PUBLIC LICENSE v3" in section
    # Build options and disabled libraries are not listed as linked in.
    for absent in ("version3", "shared", "schannel", "libx264", "prefix"):
        assert f"    {absent} -" not in section


def test_an_ffmpeg_other_than_the_pinned_build_cannot_get_notices(tmp_path):
    app = _app(tmp_path, {"av.libs/avutil-60-abc.dll": _ffmpeg_dll("libavutil", LGPL_CONFIGURE, version="8.0.1")})
    with pytest.raises(notices.MissingLicenceError, match="not the pinned build"):
        notices.ffmpeg_section(app)


def test_an_ffmpeg_without_its_licence_text_cannot_get_notices(tmp_path):
    with pytest.raises(notices.MissingLicenceError, match=lgpl_av.FFMPEG_LICENCE_FILE):
        notices.ffmpeg_section(_pinned_app(tmp_path, with_licence=False))


def test_an_app_without_ffmpeg_says_so(tmp_path):
    assert "no FFmpeg library found" in notices.ffmpeg_section(_app(tmp_path, {"python312.dll": b"MZ"}))


def _installed(configure: str = LGPL_CONFIGURE, ffmpeg: str | None = None, version: str | None = None) -> dict:
    return {
        "version": version or lgpl_av.AV_VERSION,
        "ffmpeg": ffmpeg or f"{lgpl_av.FFMPEG_VERSION}-20261031",
        "libraries": _meta(configure),
    }


def test_the_pinned_lgpl_pyav_is_not_rebuilt(monkeypatch):
    monkeypatch.setattr(lgpl_av, "installed_av", lambda: _installed())
    monkeypatch.setattr(lgpl_av, "build_wheel", lambda: pytest.fail("must not build"))
    lgpl_av.ensure_lgpl_av()


@pytest.mark.parametrize(
    ("installed", "reason"),
    [
        (None, "PyAV is not installed"),
        (_installed(PYPI_CONFIGURE, ffmpeg="8.1.2"), "--enable-libx264"),
        (_installed(ffmpeg="8.0.1"), "its FFmpeg is 8.0.1"),  # an LGPL PyAV of the same release on another FFmpeg
        (_installed(version="18.0.0"), "PyAV is 18.0.0"),
    ],
)
def test_anything_but_the_pinned_build_is_replaced(monkeypatch, tmp_path, installed, reason):
    assert any(reason in problem for problem in lgpl_av.why_not_pinned(installed))
    states = iter([installed, _installed()])
    calls = []
    monkeypatch.setattr(lgpl_av, "installed_av", lambda: next(states))
    monkeypatch.setattr(lgpl_av, "build_wheel", lambda: tmp_path / "av.whl")
    monkeypatch.setattr(lgpl_av.subprocess, "run", lambda command, **_: calls.append(command))
    lgpl_av.ensure_lgpl_av()
    assert calls and str(tmp_path / "av.whl") in calls[0]


def test_a_wheel_that_stays_gpl_after_the_install_is_an_error(monkeypatch, tmp_path):
    states = iter([None, _installed(PYPI_CONFIGURE)])
    monkeypatch.setattr(lgpl_av, "installed_av", lambda: next(states))
    monkeypatch.setattr(lgpl_av, "build_wheel", lambda: tmp_path / "av.whl")
    monkeypatch.setattr(lgpl_av.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(lgpl_av.GplError, match="still not the pinned LGPL build"):
        lgpl_av.ensure_lgpl_av()


def test_a_cached_wheel_is_only_reused_for_the_same_ffmpeg_and_python(monkeypatch):
    folder = lgpl_av.wheel_folder()
    assert lgpl_av.FFMPEG_VERSION in folder.name and lgpl_av.AV_VERSION in folder.name
    assert folder.name.endswith(f"py{sys.version_info.major}{sys.version_info.minor}")
    monkeypatch.setattr(lgpl_av, "FFMPEG_VERSION", "n9.0.0-1-gabc")
    assert lgpl_av.wheel_folder() != folder


def test_a_download_that_does_not_match_its_pin_is_an_integrity_error(monkeypatch, tmp_path):
    monkeypatch.setattr(lgpl_av, "WORK", tmp_path)
    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"truncated"))
    with pytest.raises(lgpl_av.LgplBuildError, match="is not the pinned file"):
        lgpl_av._download("https://example.invalid/ffmpeg.zip", "0" * 64)
    assert not (tmp_path / "ffmpeg.zip").exists()
    assert not issubclass(lgpl_av.LgplBuildError, lgpl_av.GplError)


def test_a_download_that_matches_its_pin_is_kept_and_reused(monkeypatch, tmp_path):
    import hashlib

    monkeypatch.setattr(lgpl_av, "WORK", tmp_path)
    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"payload"))
    digest = hashlib.sha256(b"payload").hexdigest()
    path = lgpl_av._download("https://example.invalid/av.tar.gz", digest)
    assert path.read_bytes() == b"payload"
    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not download again"))
    assert lgpl_av._download("https://example.invalid/av.tar.gz", digest) == path
