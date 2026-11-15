"""The FFmpeg source archives that go on every release: packaging/attach_ffmpeg_source.py. No network: the download,
`git` and `gh` are replaced."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from packaging_scripts import PACKAGING, load_packaging_script

lgpl_av = load_packaging_script("lgpl_av")  # first: the script imports it by name
attach = load_packaging_script("attach_ffmpeg_source")

TARS = {"ffmpeg-source.tar.gz": b"ffmpeg source tar", "scripts.tar.gz": b"build scripts tar"}
COMMIT = "a" * 40


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Two fake archives served instead of the real ones; returns the URLs fetched and the bodies served (the pinned
    digest is the one of the uncompressed tar, the body is its gzip)."""
    archives = tuple((name, f"https://example.invalid/{name}", _digest(tar)) for name, tar in TARS.items())
    monkeypatch.setattr(lgpl_av, "SOURCE_ARCHIVES", archives)
    monkeypatch.setattr(lgpl_av, "WORK", tmp_path / "work")
    fetched: list[str] = []
    bodies = {url: gzip.compress(tar) for (_, url, _), tar in zip(archives, TARS.values(), strict=True)}

    def urlopen(request, timeout=None):
        fetched.append(request.full_url)
        return io.BytesIO(bodies[request.full_url])

    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", urlopen)
    return fetched, bodies


class Tools:
    """Stands in for git and gh: records the upload and answers the questions the script asks."""

    def __init__(self):
        self.head = COMMIT
        self.tagged: str | None = COMMIT
        self.release: dict | None = {"isDraft": True, "assets": [{"name": attach.INSTALLER}]}
        self.uploads: list[list[str]] = []

    def run(self, command, **kw):
        out = ""
        if command[0] == "git":
            if command[1:3] == ["rev-parse", "--verify"]:
                if self.tagged is None:
                    raise subprocess.CalledProcessError(128, command)
                out = self.tagged
            else:
                out = self.head
        elif command[1:3] == ["release", "view"]:
            if self.release is None:
                raise subprocess.CalledProcessError(1, command)
            out = json.dumps(self.release)
        elif command[1:3] == ["release", "upload"]:
            self.uploads.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=out + "\n")


@pytest.fixture
def tools(monkeypatch):
    fake = Tools()
    monkeypatch.setattr(attach.shutil, "which", lambda name: "/bin/gh")
    monkeypatch.setattr(attach.subprocess, "run", fake.run)
    return fake


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
        assert re.fullmatch(r"[0-9a-f]{64}", sha256)


def test_the_notices_name_the_release_assets():
    for name, _, _ in lgpl_av.SOURCE_ARCHIVES:
        assert name in lgpl_av.FFMPEG_SOURCE


def test_every_archive_is_downloaded_verified_and_named_for_the_release(served):
    files = attach.fetch_sources()
    assert [f.name for f in files] == list(TARS)
    assert [gzip.decompress(f.read_bytes()) for f in files] == list(TARS.values())
    assert not list(lgpl_av.WORK.glob("*.part"))


def test_the_pin_is_the_content_not_the_compression(served):
    _, bodies = served
    for url, tar in zip(bodies, TARS.values(), strict=True):
        bodies[url] = gzip.compress(tar, compresslevel=1, mtime=12345)  # another gzip of the same tar
    assert len(attach.fetch_sources()) == len(TARS)


def test_the_upload_replaces_assets_of_the_same_name(served, tools, capsys):
    assert attach.main(["v1.2.3"]) == 0
    [command] = tools.uploads
    assert command[:4] == ["/bin/gh", "release", "upload", "v1.2.3"]
    assert [Path(f).name for f in command[4:-1]] == list(TARS)
    assert command[-1] == "--clobber"
    assert "Attached" in capsys.readouterr().out


def test_a_second_run_reuses_the_verified_downloads(served, tools):
    fetched, _ = served
    assert attach.main(["v1.2.3"]) == 0
    assert attach.main(["v1.2.3"]) == 0
    assert len(fetched) == len(TARS)  # downloaded once
    assert len(tools.uploads) == 2  # uploaded twice: --clobber makes that safe


def test_a_dry_run_with_a_tag_checks_but_uploads_nothing(served, tools, capsys):
    assert attach.main(["v1.2.3", "--dry-run"]) == 0
    assert tools.uploads == []
    assert "gh release upload v1.2.3" in capsys.readouterr().out


def test_a_dry_run_without_a_tag_only_verifies_the_archives(served, monkeypatch, capsys):
    def no_tools(*args, **kw):
        raise AssertionError("no git or gh without a tag")

    monkeypatch.setattr(attach.subprocess, "run", no_tools)
    assert attach.main(["--dry-run"]) == 0
    assert "Verified" in capsys.readouterr().out


def test_without_a_tag_a_real_run_is_refused(served, tools):
    with pytest.raises(SystemExit):
        attach.main([])
    assert tools.uploads == []


def test_a_checkout_that_is_not_the_tag_uploads_nothing(served, tools, capsys):
    tools.head = "b" * 40  # main after the pins moved on
    assert attach.main(["v1.2.3"]) == 1
    assert tools.uploads == []
    assert "is not v1.2.3" in capsys.readouterr().err


def test_a_tag_missing_from_the_checkout_uploads_nothing(served, tools, capsys):
    tools.tagged = None
    assert attach.main(["v1.2.3"]) == 1
    assert tools.uploads == []
    assert "git fetch --tags" in capsys.readouterr().err


def test_a_release_without_the_installer_gets_no_source(served, tools, capsys):
    tools.release = {"isDraft": True, "assets": [{"name": "nexusvox-1.2.3.whl"}]}
    assert attach.main(["v1.2.3"]) == 1
    assert tools.uploads == []
    assert attach.INSTALLER in capsys.readouterr().err


def test_a_release_that_does_not_exist_gets_no_source(served, tools, capsys):
    tools.release = None  # a typo in the tag
    assert attach.main(["v1.2.4"]) == 1
    assert tools.uploads == []
    assert "no release for v1.2.4" in capsys.readouterr().err


def test_a_published_release_is_left_alone_unless_asked(served, tools, capsys):
    tools.release = {"isDraft": False, "assets": [{"name": attach.INSTALLER}]}
    assert attach.main(["v1.2.3"]) == 1
    assert tools.uploads == []
    assert "already published" in capsys.readouterr().err
    assert attach.main(["v1.2.3", "--allow-published"]) == 0
    assert len(tools.uploads) == 1


def test_an_archive_that_does_not_match_its_digest_stops_before_any_upload(served, tools, capsys):
    _, bodies = served
    bodies[lgpl_av.SOURCE_ARCHIVES[1][1]] = gzip.compress(b"something else")
    assert attach.main(["v1.2.3"]) == 1
    assert tools.uploads == []
    assert "is not the pinned file" in capsys.readouterr().err
    assert list(lgpl_av.WORK.glob(lgpl_av.SOURCE_ARCHIVES[1][0] + "*")) == []  # the bad file is not kept


def test_a_download_cut_short_leaves_nothing_under_the_release_name(served, tools):
    _, bodies = served
    url = lgpl_av.SOURCE_ARCHIVES[0][1]
    bodies[url] = bodies[url][:-6]  # a truncated gzip
    assert attach.main(["v1.2.3"]) == 1
    assert tools.uploads == []
    assert list(lgpl_av.WORK.glob(lgpl_av.SOURCE_ARCHIVES[0][0] + "*")) == []


def test_a_failing_download_is_reported_not_raised(served, tools, monkeypatch, capsys):
    class Dropped(io.BytesIO):
        def read(self, size=-1):
            raise OSError("connection reset")

    monkeypatch.setattr(lgpl_av.urllib.request, "urlopen", lambda request, timeout=None: Dropped())
    assert attach.main(["v1.2.3"]) == 1
    assert "connection reset" in capsys.readouterr().err
    assert tools.uploads == []
    assert list(lgpl_av.WORK.glob("*" + lgpl_av.SOURCE_ARCHIVES[0][0] + "*")) == []  # no .part left


def test_a_failing_upload_is_reported_with_exit_code_1(served, tools, monkeypatch, capsys):
    def failing(command, **kw):
        if command[1:3] == ["release", "upload"]:
            raise subprocess.CalledProcessError(1, command)
        return tools.run(command, **kw)

    monkeypatch.setattr(attach.subprocess, "run", failing)
    assert attach.main(["v1.2.3"]) == 1
    assert "error:" in capsys.readouterr().err


def test_a_missing_gh_is_reported(served, tools, monkeypatch, capsys):
    monkeypatch.setattr(attach.shutil, "which", lambda name: None)
    assert attach.main(["v1.2.3"]) == 1
    assert "GitHub CLI" in capsys.readouterr().err


def test_the_workflows_check_the_pins_early_and_attach_on_the_draft():
    workflows = PACKAGING.parent / ".github" / "workflows"
    installer = (workflows / "installer.yml").read_text(encoding="utf-8")
    release = (workflows / "release.yml").read_text(encoding="utf-8")
    assert "attach_ffmpeg_source.py --dry-run" in installer
    assert installer.index("attach_ffmpeg_source.py --dry-run") < installer.index("Build the app")
    # After the draft exists, from the checkout of the tag.
    assert release.index("Create GitHub Release") < release.index('attach_ffmpeg_source.py "$GITHUB_REF_NAME"')
