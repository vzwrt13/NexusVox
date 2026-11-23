"""No speech model is downloaded until the user starts it: the consent, the download job, the engines, app start,
a hotkey press, and the dashboard's Download box. Nothing here downloads: the model loaders are fakes."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dashboard_js import STATIC

from nexusvox.config import Config, InferenceConfig
from nexusvox.transcribers import auto_parakeet
from nexusvox.transcribers.local_parakeet import LocalParakeetTranscriber
from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber
from nexusvox.transcription.model_download import MISSING_MESSAGE, OFFLINE_MESSAGE, ModelNotDownloaded

PARAKEET = "parakeet-tdt-0.6b"
WHISPER = "whisper-large-v3-turbo"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


@pytest.fixture(autouse=True)
def _clean_parakeet():
    LocalParakeetTranscriber._model_cache.clear()
    LocalParakeetTranscriber._set_status("idle")
    LocalParakeetTranscriber._preloading = False
    LocalWhisperTranscriber._model_cache.clear()
    yield
    LocalParakeetTranscriber._model_cache.clear()
    LocalParakeetTranscriber._set_status("idle")
    LocalParakeetTranscriber._preloading = False
    LocalWhisperTranscriber._model_cache.clear()


def _on_disk(monkeypatch, md, present: bool | None) -> None:
    """Whether the models are in the cache, for every check: the consent's and the Parakeet engine's own."""
    state = {"downloaded": present, "size": "~670 MB"}
    monkeypatch.setattr(md, "model_download_state", lambda model_id, cache_dir=None: state)
    monkeypatch.setattr("nexusvox.transcribers.local_parakeet.model_download_state", lambda model_id: state)


class _OnnxAsr:
    """Stands in for the onnx_asr module: records each load instead of downloading."""

    def __init__(self, error: Exception | None = None) -> None:
        self.loads: list[str] = []
        self.error = error

    def load_model(self, name, **kwargs):
        self.loads.append(name)
        if self.error is not None:
            raise self.error
        return object()


def _parakeet() -> LocalParakeetTranscriber:
    return LocalParakeetTranscriber(InferenceConfig(model=PARAKEET, device="cpu"))


def _wait_for(md, model_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while md.job(model_id)["state"] == "downloading" and time.monotonic() < deadline:
        time.sleep(0.01)
    return md.job(model_id)


# ── Consent ─────────────────────────────────────────────────────────────────────────────────────


def test_a_missing_model_is_refused_until_the_user_starts_its_download(ask_before_download, monkeypatch):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)

    assert md.missing(PARAKEET)
    with pytest.raises(ModelNotDownloaded, match="not downloaded yet"):
        md.require(PARAKEET)

    assert md.start(PARAKEET, lambda: None)
    assert _wait_for(md, PARAKEET)["state"] == "ready"
    assert not md.missing(PARAKEET)
    md.require(PARAKEET)  # allowed now


def test_a_model_on_disk_or_one_that_cannot_be_checked_needs_no_consent(ask_before_download, monkeypatch):
    md = ask_before_download
    for present in (True, None):
        _on_disk(monkeypatch, md, present)
        assert not md.missing(PARAKEET)
    assert not md.missing(None)  # a server engine has no model of its own


def test_a_download_that_fails_offline_says_so_and_takes_the_permission_back(ask_before_download, monkeypatch):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)

    class LocalEntryNotFoundError(Exception):  # huggingface_hub's, matched by name
        pass

    def offline():
        raise LocalEntryNotFoundError("cannot reach huggingface.co")

    md.start(PARAKEET, offline)
    job = _wait_for(md, PARAKEET)

    assert job == {"state": "error", "error": OFFLINE_MESSAGE, "offline": True}
    assert md.missing(PARAKEET)  # only the next click (Retry) downloads again


def test_retry_after_a_failure_downloads_again(ask_before_download, monkeypatch):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)
    attempts: list[int] = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("disk full")

    md.start(PARAKEET, flaky)
    assert _wait_for(md, PARAKEET) == {"state": "error", "error": "The download failed: disk full", "offline": False}
    md.start(PARAKEET, flaky)  # Retry
    assert _wait_for(md, PARAKEET)["state"] == "ready"
    assert len(attempts) == 2


def test_a_second_click_while_it_downloads_starts_nothing(ask_before_download):
    import threading

    md = ask_before_download
    release = threading.Event()
    assert md.start(PARAKEET, lambda: release.wait(2))
    assert not md.start(PARAKEET, lambda: pytest.fail("a second download"))
    release.set()
    assert _wait_for(md, PARAKEET)["state"] == "ready"


@pytest.mark.parametrize(
    "exc",
    [ConnectionError("refused"), TimeoutError("slow"), OSError("x"), RuntimeError("wrapped")],
)
def test_offline_is_told_from_the_exception_chain(exc):
    from nexusvox.transcription.model_download import is_offline

    if isinstance(exc, RuntimeError):
        try:
            try:
                raise ConnectionError("no route")
            except ConnectionError as inner:
                raise exc from inner
        except RuntimeError as outer:
            exc = outer
        assert is_offline(exc)
    else:
        assert is_offline(exc) == (type(exc) is not OSError)


def test_progress_counts_the_bytes_in_the_cache(monkeypatch, tmp_path):
    from nexusvox.transcription import model_download as md

    blobs = tmp_path / "models--istupakov--parakeet-tdt-0.6b-v3-onnx" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "a.incomplete").write_bytes(b"\0" * 1000)
    monkeypatch.setattr(md, "_hub_cache", lambda: tmp_path)
    monkeypatch.setitem(md.MODEL_REGISTRY[PARAKEET], "download_bytes", 4000)

    assert md.downloaded_bytes(PARAKEET) == 1000
    assert md.progress(PARAKEET) == 25
    (blobs / "b").write_bytes(b"\0" * 5000)
    assert md.progress(PARAKEET) == 99  # never 100 before the load is done
    assert md.progress("custom-server") is None


# ── Engines ─────────────────────────────────────────────────────────────────────────────────────


def test_parakeet_does_not_download_without_consent(ask_before_download, monkeypatch):
    _on_disk(monkeypatch, ask_before_download, False)
    onnx = _OnnxAsr()
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", onnx)

    with pytest.raises(ModelNotDownloaded):
        LocalParakeetTranscriber._get_model("nemo-parakeet-tdt-0.6b-v3", "int8")
    assert onnx.loads == []
    assert LocalParakeetTranscriber.load_status()["state"] == "idle"  # no download shown that never runs


@pytest.mark.asyncio
async def test_a_parakeet_connect_without_the_model_fails_before_recording(ask_before_download, monkeypatch):
    _on_disk(monkeypatch, ask_before_download, False)
    onnx = _OnnxAsr()
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", onnx)
    t = _parakeet()

    with pytest.raises(ModelNotDownloaded):
        await t.connect()
    await asyncio.sleep(0.05)
    assert not t.connected
    assert onnx.loads == []  # no background load either


def test_parakeet_downloads_after_the_click(ask_before_download, monkeypatch):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)
    onnx = _OnnxAsr()
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", onnx)
    t = _parakeet()

    md.start(t.local_model, t.download_model)

    assert _wait_for(md, PARAKEET)["state"] == "ready"
    assert onnx.loads == ["nemo-parakeet-tdt-0.6b-v3"]
    assert LocalParakeetTranscriber.load_status()["state"] == "ready"


@pytest.mark.asyncio
async def test_a_parakeet_model_on_disk_loads_as_before(ask_before_download, monkeypatch):
    _on_disk(monkeypatch, ask_before_download, True)
    onnx = _OnnxAsr()
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", onnx)

    await _parakeet().connect()
    await asyncio.sleep(0.1)
    assert onnx.loads == ["nemo-parakeet-tdt-0.6b-v3"]


@pytest.mark.asyncio
async def test_whisper_does_not_download_without_consent(ask_before_download, monkeypatch):
    _on_disk(monkeypatch, ask_before_download, False)
    loads: list[tuple] = []
    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: loads.append(a)))
    t = LocalWhisperTranscriber(InferenceConfig(model=WHISPER), device="cpu", compute_type="int8")

    with pytest.raises(ModelNotDownloaded):
        await t.connect()
    await asyncio.sleep(0.05)
    assert loads == [] and not t.connected
    assert t.local_model == WHISPER


def test_the_auto_engine_does_not_preload_a_missing_cpu_copy(ask_before_download, monkeypatch, caplog):
    _on_disk(monkeypatch, ask_before_download, False)
    onnx = _OnnxAsr()
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", onnx)
    t = auto_parakeet.AutoParakeetTranscriber(InferenceConfig(model=PARAKEET, device="auto"))

    with caplog.at_level("INFO"):
        t._preload_cpu()
        t._preload_thread.join(2)
        time.sleep(0.05)
    assert onnx.loads == []
    assert "not downloaded" in caplog.text
    assert "Preloading the CPU Parakeet model failed" not in caplog.text
    assert t.local_model == PARAKEET


def test_server_engines_have_nothing_to_download():
    from nexusvox.transcription.transcriber import OpenAIHttpTranscriber

    t = OpenAIHttpTranscriber(InferenceConfig(model=PARAKEET))
    assert t.local_model is None
    with pytest.raises(NotImplementedError):
        t.download_model()


# ── App: start and hotkey ───────────────────────────────────────────────────────────────────────


def _app_cls():
    try:
        from nexusvox.app.core import NexusVoxApp
    except Exception:  # Windows-only imports (pynput hook, tray, SendInput) on other platforms
        pytest.skip("nexusvox.app.core needs the Windows runtime")
    return NexusVoxApp


def test_app_start_warm_up_downloads_nothing(ask_before_download, monkeypatch, caplog):
    _on_disk(monkeypatch, ask_before_download, False)
    onnx = _OnnxAsr()
    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", onnx)
    app = _app_cls().__new__(_app_cls())
    app._transcriber = _parakeet()

    with caplog.at_level("INFO"):
        asyncio.run(app._preload_transcriber())
    time.sleep(0.05)

    assert onnx.loads == []
    assert "waits for the user to download it" in caplog.text
    assert "Preloading transcriber failed" not in caplog.text


def test_the_status_says_the_model_waits_for_its_download(ask_before_download, monkeypatch):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)
    app = _app_cls().__new__(_app_cls())
    app._switch_status, app._switch_error, app._switch_note = "idle", None, None
    app._transcriber = _parakeet()

    assert app.get_switch_status() == {"status": "missing", "error": None}
    _on_disk(monkeypatch, md, True)
    assert app.get_switch_status() == {"status": "idle", "error": None}


def _press_app(monkeypatch, *, setup_done: bool):
    app_cls = _app_cls()  # skips first: the app's parts need the Windows runtime
    from app_parts import patch_app

    patch_app(monkeypatch, "running_dashboard_api", lambda: None)
    patch_app(monkeypatch, "_play", lambda beep: None)
    app = app_cls.__new__(app_cls)
    app._transcriber = MagicMock(connect=AsyncMock(side_effect=ModelNotDownloaded(PARAKEET)), disconnect=AsyncMock())
    app._hotkey = MagicMock(command=False, mark=None, active=False, mode="hold")
    app._record_start_event = asyncio.Event()
    app._stop_info = None
    app._config = MagicMock(hotkey=MagicMock(max_recording_s=600), setup_done=setup_done)
    app._press_time = 100.0
    app._audio = MagicMock()
    app._mic_guard = None
    app._tray = MagicMock()
    app._overlay = None
    app._offline_stop = AsyncMock()
    app._model_hint_at = None
    app._loop = MagicMock()
    return app


@pytest.mark.parametrize(("setup_done", "tab"), [(True, "models"), (False, "")])
def test_a_press_without_the_model_hints_at_the_download_and_records_nothing(monkeypatch, caplog, setup_done, tab):
    app = _press_app(monkeypatch, setup_done=setup_done)

    async def press():
        app._record_stop_event = asyncio.Event()
        await app._transcription_cycle()

    with caplog.at_level("WARNING"):
        asyncio.run(press())

    app._audio.start.assert_not_called()  # nothing recorded, nothing downloaded
    app._offline_stop.assert_awaited_once_with(100.0)  # the error cue; paste-last and a polish tap still work
    app._loop.run_in_executor.assert_called_once_with(None, app._open_dashboard_logged, tab)
    assert MISSING_MESSAGE in caplog.text


def test_repeated_presses_open_the_dashboard_once_a_minute(monkeypatch):
    app = _press_app(monkeypatch, setup_done=True)

    for _ in range(3):
        app._hint_model_download()
    assert app._loop.run_in_executor.call_count == 1
    app._model_hint_at -= 61
    app._hint_model_download()
    assert app._loop.run_in_executor.call_count == 2


# ── Dashboard ───────────────────────────────────────────────────────────────────────────────────


def _api(db, session_factory, transcriber=None, **inference):
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    for key, value in inference.items():
        setattr(config.inference, key, value)
    return DashboardAPI(
        session_factory, config, db=db, get_transcriber=(lambda: transcriber) if transcriber is not None else None
    )


class _Engine:
    """The running engine as the dashboard sees it: its local model and a load that records instead of downloading."""

    needs_docker = False

    def __init__(self, local_model: str | None = PARAKEET, error: Exception | None = None) -> None:
        self.local_model = local_model
        self.loads = 0
        self.error = error

    def download_model(self) -> None:
        self.loads += 1
        if self.error is not None:
            raise self.error


def test_the_box_shows_hardware_model_and_size_before_anything_downloads(
    db, session_factory, ask_before_download, monkeypatch, patch_api
):
    _on_disk(monkeypatch, ask_before_download, False)
    patch_api("cuda_available", lambda: False)
    engine = _Engine()

    got = _api(db, session_factory, engine, model=PARAKEET, device="cpu").get_model_download()

    assert got == {
        "hardware": "cpu",
        "model": PARAKEET,
        "name": "Parakeet TDT 0.6B",
        "size": "~670 MB",
        "runs": "local",
        "backup": False,
        "state": "missing",
        "progress": None,
        "error": None,
        "offline": False,
    }
    assert engine.loads == 0


def test_download_starts_on_the_click_and_ends_ready(db, session_factory, ask_before_download, monkeypatch, patch_api):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)
    patch_api("cuda_available", lambda: False)
    engine = _Engine()
    api = _api(db, session_factory, engine, model=PARAKEET, device="cpu")

    assert api.start_model_download()["state"] in ("downloading", "ready")
    _wait_for(md, PARAKEET)
    assert engine.loads == 1
    assert api.get_model_download()["state"] == "ready"


def test_a_failed_download_offers_retry(db, session_factory, ask_before_download, monkeypatch, patch_api):
    md = ask_before_download
    _on_disk(monkeypatch, md, False)
    patch_api("cuda_available", lambda: False)
    engine = _Engine(error=ConnectionError("no network"))
    api = _api(db, session_factory, engine, model=PARAKEET, device="cpu")

    api.start_model_download()
    _wait_for(md, PARAKEET)
    got = api.get_model_download()
    assert (got["state"], got["offline"], got["error"]) == ("error", True, OFFLINE_MESSAGE)

    engine.error = None
    api.start_model_download()  # Retry
    _wait_for(md, PARAKEET)
    assert engine.loads == 2 and api.get_model_download()["state"] == "ready"


def test_a_model_on_disk_shows_ready_and_the_click_downloads_nothing(
    db, session_factory, ask_before_download, monkeypatch, patch_api
):
    _on_disk(monkeypatch, ask_before_download, True)
    patch_api("cuda_available", lambda: False)
    engine = _Engine()
    api = _api(db, session_factory, engine, model=PARAKEET, device="cpu")

    assert api.get_model_download()["state"] == "ready"
    api.start_model_download()
    assert engine.loads == 0


def test_docker_and_custom_server_have_nothing_to_download(db, session_factory, monkeypatch, patch_api):
    patch_api("cuda_available", lambda: True)

    docker = _api(db, session_factory, _Engine(local_model=None), model=WHISPER, device="cuda").get_model_download()
    assert (docker["hardware"], docker["runs"], docker["state"]) == ("gpu", "docker", "none")

    custom_api = _api(db, session_factory, _Engine(local_model=None), model="custom-server")
    custom_api._config.inference.custom.url = "http://127.0.0.1:9000/v1/audio/transcriptions"
    assert custom_api.get_model_download()["runs"] == "custom"
    refused = custom_api.start_model_download()
    assert refused["refused"] and refused["state"] == "none"


def test_the_auto_engine_offers_its_cpu_copy_as_a_backup(
    db, session_factory, ask_before_download, monkeypatch, patch_api
):
    _on_disk(monkeypatch, ask_before_download, False)
    patch_api("cuda_available", lambda: True)

    got = _api(db, session_factory, _Engine(), model=PARAKEET, device="cuda").get_model_download()
    assert (got["runs"], got["backup"], got["state"]) == ("docker", True, "missing")


def test_the_routes(flask_client, monkeypatch):
    state = flask_client.get("/api/models/download")
    assert state.status_code == 200 and "state" in state.get_json()
    refused = flask_client.post("/api/models/download", json={})  # no running app: no engine to download for
    assert refused.status_code == 400 and refused.get_json()["refused"]


# ── Page ────────────────────────────────────────────────────────────────────────────────────────

MODELS_JS = (STATIC / "dashboard-settings-models.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_settings_models_holds_the_same_box():
    card = HTML[HTML.index('id="settings-card-models"') : HTML.index('id="llm-model-title"')]
    assert 'id="model-download"' in card
    assert 'mountModelDownload(document.getElementById("model-download")' in MODELS_JS


def _run_box_js(body: str):
    import json

    helpers = "".join(
        re.search(rf"^function {name}\(.*?^}}\n", MODELS_JS, re.S | re.M).group(0)
        for name in ("describeModelRuntime", "describeModelDownload")
    )
    out = subprocess.run(
        ["node", "-e", f"{helpers}\n{body}"], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    )
    return json.loads(out.stdout)


@needs_node
def test_the_box_names_the_size_and_says_nothing_downloads_before_the_click():
    d = '{ name: "Parakeet TDT 0.6B", size: "~670 MB", runs: "local", backup: false, state: "missing" }'
    got = _run_box_js(
        f"const d = {d}; console.log(JSON.stringify([describeModelRuntime(d), describeModelDownload(d)]))"
    )
    assert "~670 MB" in got[0] and "Nothing is downloaded until you click Download" in got[0]
    assert got[1]["state"] == "warn"


@needs_node
def test_the_box_shows_progress_ready_and_the_offline_error():
    got = _run_box_js(
        'const base = { name: "Parakeet", size: "~670 MB", runs: "local", backup: false };'
        "console.log(JSON.stringify(["
        ' describeModelDownload({ ...base, state: "downloading", progress: 42 }),'
        ' describeModelDownload({ ...base, state: "ready" }),'
        ' describeModelDownload({ ...base, state: "error", error: "offline, click Retry" }),'
        ' describeModelDownload({ ...base, state: "none" })]))'
    )
    assert got[0] == {"state": "", "text": "Downloading Parakeet... 42%"}
    assert got[1]["state"] == "ok"
    assert got[2] == {"state": "down", "text": "✕ offline, click Retry"}
    assert got[3] is None


@needs_node
def test_home_says_the_model_waits_for_its_download():
    source = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    helpers = re.search(r"^const HOME_BUSY_PHASES = .*?;$", source, re.M).group(0)
    helpers += re.search(r"^function describeHomeStatus\(.*?^}\n", source, re.S | re.M).group(0)
    body = 'console.log(JSON.stringify(describeHomeStatus({ name: "Parakeet" }, "cpu", { status: "missing" })))'
    out = subprocess.run(
        ["node", "-e", f"{helpers}\n{body}"], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    )
    got = __import__("json").loads(out.stdout)
    assert got["state"] == "warn" and "not downloaded yet" in got["text"]


def test_readme_no_longer_promises_a_download_on_first_use():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "It downloads on first use" not in readme
