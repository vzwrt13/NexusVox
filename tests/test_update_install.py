"""*Install update*: nothing before the click, only GitHub's installer, only a valid signature, cancel and offline.

No test reaches the network (httpx.MockTransport stands in for GitHub) and none starts a process.
"""

import hashlib
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest

from nexusvox.system import update_check, update_install
from nexusvox.system.authenticode import Signature, check_signature
from nexusvox.system.update_install import SIGNER, UpdateError, UpdateInstaller, download_installer, is_allowed_hop

TAG = "v9.0.0"
URL = f"https://github.com/vzwrt13/NexusVox/releases/download/{TAG}/NexusVox-Setup.exe"
ASSET_HOST_URL = "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc?sig=x"
PAYLOAD = b"MZ" + bytes(range(256)) * 40
GOOD = Signature(True, signer=SIGNER, organisation=SIGNER, timestamped=True)


def _github(payload=PAYLOAD, redirect=ASSET_HOST_URL, seen=None):
    """GitHub as the app sees it: the release URL redirects to the asset host, which serves the file."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if str(request.url) == URL:
            return httpx.Response(302, headers={"location": redirect})
        if str(request.url) == redirect:
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _offline():
    def handler(request):
        raise httpx.ConnectError("no network", request=request)

    return httpx.MockTransport(handler)


def _check(size=None, sha256=None, url=URL):
    return {
        "ok": True,
        "newer": True,
        "latest": "9.0.0",
        "installer": {"url": url, "size": size or len(PAYLOAD), "sha256": sha256},
    }


def _installer(tmp_path, transport, launched=None, quits=None, can_install=True):
    return UpdateInstaller(
        (lambda: quits.append(1)) if quits is not None else (lambda: None),
        folder=lambda: tmp_path / "updates",
        can_install=lambda: can_install,
        transport=transport,
        launcher=lambda setup, app, pid: launched.append((setup, app, pid)) if launched is not None else None,
        quit_delay_s=0,
    )


def _left(tmp_path):
    """The files left in the updates folder."""
    folder = tmp_path / "updates"
    return list(folder.iterdir()) if folder.exists() else []


def _finish(installer):
    installer._thread.join(5)
    assert not installer._thread.is_alive()
    return installer.status()


@pytest.fixture
def signature(monkeypatch):
    """The signature every downloaded file reads as; GOOD unless a test says otherwise."""
    holder = {"value": GOOD}
    monkeypatch.setattr(update_install, "check_signature", lambda path: holder["value"])
    return holder


# ── The release's installer, read from the update check ──────────────


def _release(monkeypatch, assets):
    body = {"tag_name": TAG, "html_url": f"https://github.com/vzwrt13/NexusVox/releases/tag/{TAG}", "assets": assets}
    monkeypatch.setattr(
        update_check.httpx, "get", lambda url, **kw: httpx.Response(200, json=body, request=httpx.Request("GET", url))
    )


def test_check_reports_the_installer_with_its_size_and_digest(monkeypatch):
    digest = "a" * 64
    _release(
        monkeypatch,
        [
            {"name": "nexusvox-9.0.0.tar.gz", "browser_download_url": "x", "size": 5},
            {"name": "NexusVox-Setup.exe", "browser_download_url": URL, "size": 123, "digest": f"sha256:{digest}"},
        ],
    )
    result = update_check.check_for_update("1.0.0")
    assert result["installer"] == {"url": URL, "size": 123, "sha256": digest}


def test_check_offers_no_installer_for_the_same_version(monkeypatch):
    _release(monkeypatch, [{"name": "NexusVox-Setup.exe", "browser_download_url": URL, "size": 123}])
    assert update_check.check_for_update("9.0.0")["installer"] is None


@pytest.mark.parametrize(
    "asset",
    [
        {"name": "NexusVox-Setup.exe", "browser_download_url": "https://evil.example/NexusVox-Setup.exe", "size": 1},
        {"name": "NexusVox-Setup.exe", "browser_download_url": URL.replace("vzwrt13", "someone"), "size": 1},
        {"name": "NexusVox-Setup.exe", "browser_download_url": URL, "size": 0},
        {"name": "NexusVox-Setup.exe", "browser_download_url": URL, "size": "12"},
        {"name": "Other.exe", "browser_download_url": URL, "size": 12},
    ],
)
def test_check_ignores_an_installer_it_may_not_use(monkeypatch, asset):
    _release(monkeypatch, [asset])
    assert update_check.check_for_update("1.0.0")["installer"] is None


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        (URL, True),
        ("https://github.com/vzwrt13/NexusVox/releases/download/v1.0.0-rc1/NexusVox-Setup.exe", True),
        ("http://github.com/vzwrt13/NexusVox/releases/download/v9.0.0/NexusVox-Setup.exe", False),
        ("https://github.com/vzwrt13/NexusVox/releases/download/../x/NexusVox-Setup.exe", False),
        ("https://github.com/vzwrt13/NexusVox/releases/download/v9/../NexusVox-Setup.exe", False),
        ("https://github.com/vzwrt13/NexusVox/releases/download/v9.0.0/NexusVox-Setup.exe.zip", False),
        ("https://github.com/vzwrt13/NexusVox/releases/download/v9.0.0/other.exe", False),
        ("https://github.com.evil.example/vzwrt13/NexusVox/releases/download/v9.0.0/NexusVox-Setup.exe", False),
        (None, False),
    ],
)
def test_only_the_projects_installer_url_is_allowed(url, allowed):
    assert update_check.is_installer_url(url) is allowed


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        (ASSET_HOST_URL, True),
        ("https://objects.githubusercontent.com/x", True),
        ("http://release-assets.githubusercontent.com/x", False),
        ("https://release-assets.githubusercontent.com:8443/x", False),
        ("https://evil.example/x", False),
        ("https://release-assets.githubusercontent.com.evil.example/x", False),
        ("https://github.com/someone/else/archive.exe", False),
        ("https://release-assets.githubusercontent.com/x\ny", False),
    ],
)
def test_only_githubs_asset_hosts_are_followed(url, allowed):
    assert is_allowed_hop(url) is allowed


# ── Nothing before consent ───────────────────────────────────────────


def _client(session_factory, installer):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    api = DashboardAPI(session_factory, Config())
    api._updates = installer
    return _create_app(api).test_client()


def test_check_offers_the_install_but_downloads_nothing(monkeypatch, session_factory, tmp_path):
    seen: list[str] = []
    installer = _installer(tmp_path, _github(seen=seen))
    _release(monkeypatch, [{"name": "NexusVox-Setup.exe", "browser_download_url": URL, "size": len(PAYLOAD)}])
    client = _client(session_factory, installer)
    reply = client.post("/api/update-check").get_json()
    assert reply["can_install"] is True and reply["installer"]["size"] == len(PAYLOAD)
    assert client.get("/api/update/status").get_json()["state"] == "idle"
    assert seen == [] and not (tmp_path / "updates").exists()


def test_source_run_offers_no_install(monkeypatch, session_factory, tmp_path):
    _release(monkeypatch, [{"name": "NexusVox-Setup.exe", "browser_download_url": URL, "size": 10}])
    client = _client(session_factory, _installer(tmp_path, _github(), can_install=False))
    assert client.post("/api/update-check").get_json()["can_install"] is False
    assert client.post("/api/update/install").status_code == 409


def test_install_without_a_check_downloads_nothing(session_factory, tmp_path):
    seen: list[str] = []
    response = _client(session_factory, _installer(tmp_path, _github(seen=seen))).post("/api/update/install")
    assert response.status_code == 409 and response.get_json()["error"] == "no_update"
    assert seen == []


def test_the_app_without_a_quit_hook_offers_no_install(session_factory):
    from nexusvox.config import Config
    from nexusvox.dashboard.api import DashboardAPI

    assert DashboardAPI(session_factory, Config())._updates.available() is False


# ── The whole flow ───────────────────────────────────────────────────


def test_install_downloads_verifies_starts_the_installer_and_quits(tmp_path, signature):
    launched, quits, seen = [], [], []
    installer = _installer(tmp_path, _github(seen=seen), launched, quits)
    assert installer.offer(_check(sha256=hashlib.sha256(PAYLOAD).hexdigest()))
    installer.start()
    status = _finish(installer)
    assert status["state"] == "installing" and status["received"] == len(PAYLOAD)
    setup = tmp_path / "updates" / "NexusVox-Setup.exe"
    assert launched == [(setup, Path(sys.executable), os.getpid())]
    assert setup.read_bytes() == PAYLOAD
    assert seen == [URL, ASSET_HOST_URL]
    for _ in range(50):  # the quit runs on a timer thread
        if quits:
            break
        threading.Event().wait(0.02)
    assert quits == [1]


@pytest.mark.parametrize(
    ("found", "reason"),
    [
        (Signature(False, reason="the file is not signed"), "not signed"),
        (Signature(False, reason="the file was changed after it was signed"), "changed after"),
        (Signature(True, signer="Someone Else", organisation="Someone Else", timestamped=True), "Someone Else"),
        (Signature(True, signer=SIGNER, organisation=SIGNER, timestamped=False), "no timestamp"),
    ],
)
def test_bad_or_missing_signature_deletes_the_file_and_never_runs_it(tmp_path, signature, found, reason):
    signature["value"] = found
    launched, quits = [], []
    installer = _installer(tmp_path, _github(), launched, quits)
    installer.offer(_check())
    installer.start()
    status = _finish(installer)
    assert status["state"] == "failed" and reason in status["message"] and "not run" in status["message"]
    assert launched == [] and quits == []
    assert _left(tmp_path) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Authenticode is checked by Windows")
def test_an_unsigned_file_reads_as_not_valid(tmp_path):
    unsigned = tmp_path / "NexusVox-Setup.exe"
    unsigned.write_bytes(PAYLOAD)
    assert check_signature(unsigned).valid is False


def test_redirect_to_another_host_is_refused(tmp_path, signature):
    seen: list[str] = []
    launched = []
    installer = _installer(tmp_path, _github(redirect="https://evil.example/NexusVox-Setup.exe", seen=seen), launched)
    installer.offer(_check())
    installer.start()
    status = _finish(installer)
    assert status["state"] == "failed" and "not GitHub" in status["message"]
    assert seen == [URL] and launched == []
    assert _left(tmp_path) == []


def test_a_wrong_url_is_refused_before_any_request(tmp_path):
    seen: list[str] = []
    with pytest.raises(UpdateError) as caught:
        download_installer(
            "https://evil.example/NexusVox-Setup.exe", tmp_path / "s.exe", size=1, transport=_github(seen=seen)
        )
    assert caught.value.code == "refused" and seen == []
    installer = _installer(tmp_path, _github(seen=seen))
    assert installer.offer(_check(url="https://evil.example/NexusVox-Setup.exe")) is False
    assert installer.start()["error"] == "no_update" and seen == []


def test_offline_says_so_and_leaves_nothing(tmp_path, signature):
    launched = []
    installer = _installer(tmp_path, _offline(), launched)
    installer.offer(_check())
    installer.start()
    status = _finish(installer)
    assert status["state"] == "failed" and status["message"] == update_install.OFFLINE_MESSAGE
    assert launched == [] and _left(tmp_path) == []


def test_server_error_is_reported(tmp_path, signature):
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    installer = _installer(tmp_path, transport)
    installer.offer(_check())
    installer.start()
    status = _finish(installer)
    assert status["state"] == "failed" and "HTTP 503" in status["message"]


@pytest.mark.parametrize(("size", "sha256"), [(len(PAYLOAD) + 1, None), (len(PAYLOAD) - 1, None), (None, "0" * 64)])
def test_wrong_size_or_checksum_is_refused(tmp_path, signature, size, sha256):
    launched = []
    installer = _installer(tmp_path, _github(), launched)
    installer.offer(_check(size=size or len(PAYLOAD), sha256=sha256))
    installer.start()
    status = _finish(installer)
    assert status["state"] == "failed" and launched == []
    assert _left(tmp_path) == []


def test_cancel_stops_the_download_and_deletes_the_part(monkeypatch, tmp_path, signature):
    monkeypatch.setattr(update_install, "CHUNK_BYTES", 16)
    launched = []
    installer = _installer(tmp_path, _github(), launched)
    installer.offer(_check())
    original = installer._progress

    def progress(received):
        original(received)
        installer.cancel()  # the user clicks Cancel while the first chunks arrive

    installer._progress = progress
    installer.start()
    status = _finish(installer)
    assert status["state"] == "cancelled" and "cancelled" in status["message"].lower()
    assert 0 < status["received"] < len(PAYLOAD)
    assert launched == [] and _left(tmp_path) == []


def test_a_second_start_while_downloading_is_refused(tmp_path, signature):
    gate = threading.Event()

    def handler(request):
        gate.wait(5)
        return httpx.Response(404)

    installer = _installer(tmp_path, httpx.MockTransport(handler))
    installer.offer(_check())
    installer.start()
    assert installer.start()["error"] == "busy"
    assert installer.offer({"ok": True, "newer": False}) is True  # a check meanwhile keeps the running offer
    gate.set()
    _finish(installer)


def test_old_leftovers_are_removed_before_a_download(tmp_path, signature):
    folder = tmp_path / "updates"
    folder.mkdir()
    (folder / "NexusVox-Setup.exe.part").write_bytes(b"old")
    installer = _installer(tmp_path, _offline())
    installer.offer(_check())
    installer.start()
    _finish(installer)
    assert list(folder.iterdir()) == []


def test_install_route_runs_and_reports_progress(session_factory, tmp_path, signature):
    launched = []
    installer = _installer(tmp_path, _github(), launched)
    installer.offer(_check())
    client = _client(session_factory, installer)
    assert client.post("/api/update/install").status_code == 200
    _finish(installer)
    assert client.get("/api/update/status").get_json()["state"] == "installing"
    assert client.post("/api/update/cancel").get_json()["state"] == "installing"  # too late to cancel
    assert len(launched) == 1


# ── The waiter ───────────────────────────────────────────────────────


def test_launcher_passes_paths_through_the_environment(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(update_install.subprocess, "Popen", lambda command, **kw: calls.append((command, kw)))
    monkeypatch.setenv("PSModulePath", r"C:\pwsh7\Modules")
    setup = tmp_path / "it's here" / "NexusVox-Setup.exe"
    app = tmp_path / "Programs" / "NexusVox" / "NexusVox.exe"
    update_install.launch_installer(setup, app, 4242)
    ((command, kw),) = calls
    assert "windowspowershell" in command[0].lower() and command[0].lower().endswith("powershell.exe")
    assert str(setup) not in " ".join(command) and str(app) not in " ".join(command)
    env = kw["env"]
    assert env["NEXUSVOX_UPDATE_SETUP"] == str(setup) and env["NEXUSVOX_UPDATE_APP"] == str(app)
    assert env["NEXUSVOX_UPDATE_PID"] == "4242" and env["NEXUSVOX_UPDATE_ARGS"] == "/SILENT /SP- /NORESTART"
    assert "PSModulePath" not in env
    assert kw["creationflags"] & 0x08000000  # no console window


def test_launcher_failure_is_an_update_error(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(update_install.subprocess, "Popen", refuse)
    with pytest.raises(UpdateError) as caught:
        update_install.launch_installer(tmp_path / "s.exe", tmp_path / "a.exe", 1)
    assert caught.value.code == "launch"
