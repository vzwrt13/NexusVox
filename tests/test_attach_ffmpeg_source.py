"""The FFmpeg source archives that go on every release: packaging/attach_ffmpeg_source.py. No network: the download
and the `gh` call are replaced."""

from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path

import pytest
from packaging_scripts import load_packaging_script

lgpl_av = load_packaging_script("lgpl_av")  # first: the script imports it by name
attach = load_packaging_script("attach_ffmpeg_source")

FAKE = {"ffmpeg-source.tar.gz": b"ffmpeg source", "scripts.tar.gz": b"build scripts"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Two fake archives served instead of the real ones; returns the list of URLs that were fetched."""
    archives = tuple((name, f"https://example.invalid/{name}", _digest(data)) for name, data in FAKE.items())
    monkeypatch.setattr(lgpl_av, "SOURCE_ARCHIVES", archives)
    monkeypatch.setattr(lgpl_av, "WORK", tmp_path / "work")
    fetched: list[str] = []
    bodies = dict(zip((url for _, url, _ in archives), FAKE.values(), strict=True))

    def urlopen(request, timeout=None):
        fetched.append(request.full_url)
        return io.BytesIO(bodies[request.full_url])

    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", urlopen)
    return fetched, bodies


@pytest.fixture
def gh_calls(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(attach.shutil, "which", lambda name: "/bin/gh")
    monkeypatch.setattr(attach.subprocess, "run", lambda command, **kw: calls.append(command))
    return calls


def test_the_pinned_source_is_the_pinned_ffmpeg():
    names = [name for name, _, _ in lgpl_av.SOURCE_ARCHIVES]
    # The FFmpeg version carries the abbreviated commit; the archive must be of that commit.
    assert lgpl_av.FFMPEG_VERSION.rsplit("-g", 1)[1] == lgpl_av.FFMPEG_COMMIT[:10]
    assert lgpl_av.FFMPEG_COMMIT in lgpl_av.SOURCE_ARCHIVES[0][1]
    assert lgpl_av.BTBN_COMMIT in lgpl_av.SOURCE_ARCHIVES[1][1]
    assert lgpl_av.FFMPEG_VERSION in names[0]
    assert lgpl_av.BTBN_RELEASE in names[1]
    assert len(set(names)) == len(names)
    for _, url, sha256 in lgpl_av.SOURCE_ARCHIVES:
        assert url.startswith("https://github.com/")
        assert len(sha256) == 64 and int(sha256, 16) >= 0


def test_the_notices_name_the_release_assets():
    for name, _, _ in lgpl_av.SOURCE_ARCHIVES:
        assert name in lgpl_av.FFMPEG_SOURCE


def test_every_archive_is_downloaded_verified_and_named_for_the_release(served):
    files = attach.fetch_sources()
    assert [f.name for f in files] == list(FAKE)
    assert [f.read_bytes() for f in files] == list(FAKE.values())


def test_the_upload_replaces_assets_of_the_same_name(served, gh_calls, capsys):
    assert attach.main(["v1.2.3"]) == 0
    [command] = gh_calls
    assert command[:4] == ["/bin/gh", "release", "upload", "v1.2.3"]
    assert [Path(f).name for f in command[4:-1]] == list(FAKE)
    assert command[-1] == "--clobber"
    assert "Attached" in capsys.readouterr().out


def test_a_second_run_reuses_the_verified_downloads(served, gh_calls):
    fetched, _ = served
    assert attach.main(["v1.2.3"]) == 0
    assert attach.main(["v1.2.3"]) == 0
    assert len(fetched) == len(FAKE)  # downloaded once
    assert len(gh_calls) == 2  # uploaded twice: --clobber makes that safe


def test_a_dry_run_uploads_nothing(served, gh_calls, capsys):
    assert attach.main(["v1.2.3", "--dry-run"]) == 0
    assert gh_calls == []
    assert "gh release upload v1.2.3" in capsys.readouterr().out


def test_an_archive_that_does_not_match_its_digest_stops_before_any_upload(served, gh_calls, capsys):
    _, bodies = served
    url = lgpl_av.SOURCE_ARCHIVES[1][1]
    bodies[url] = b"something else"
    assert attach.main(["v1.2.3"]) == 1
    assert gh_calls == []
    assert "is not the pinned file" in capsys.readouterr().err
    assert not (lgpl_av.WORK / lgpl_av.SOURCE_ARCHIVES[1][0]).exists()  # the bad file is not kept


def test_a_failing_download_is_reported_not_raised(served, gh_calls, monkeypatch, capsys):
    def broken(request, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", broken)
    assert attach.main(["v1.2.3"]) == 1
    assert "offline" in capsys.readouterr().err
    assert gh_calls == []


def test_a_failing_gh_is_reported_with_exit_code_1(served, monkeypatch, capsys):
    def failing(command, **kw):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(attach.shutil, "which", lambda name: "/bin/gh")
    monkeypatch.setattr(attach.subprocess, "run", failing)
    assert attach.main(["v1.2.3"]) == 1
    assert "error:" in capsys.readouterr().err


def test_a_missing_gh_is_reported(served, monkeypatch, capsys):
    monkeypatch.setattr(attach.shutil, "which", lambda name: None)
    assert attach.main(["v1.2.3"]) == 1
    assert "GitHub CLI" in capsys.readouterr().err
