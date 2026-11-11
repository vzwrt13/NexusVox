"""The LLM dropdown's data: Ollama's model list, the model-aware status and the model pull."""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error

import pytest

from nexusvox import llm
from nexusvox.config import LlmConfig

TAGS = {"models": [{"name": "gemma4:e4b", "size": 6_600_000_000}, {"name": "qwen3.5:4b", "size": 3_300_000_000}]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _serve(monkeypatch, body):
    def fake(request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        if isinstance(body, Exception):
            raise body
        return _Resp(body(url) if callable(body) else json.dumps(body).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)


def test_list_building_from_tags(monkeypatch):
    _serve(monkeypatch, TAGS)
    data = llm.build_model_list(LlmConfig(model="gemma4:e4b"), cuda=True, vram_gb=16)
    names = {m["name"]: m for m in data["recommended"]}
    assert data["reachable"] is True
    assert names["gemma4:e4b"]["installed"] and names["gemma4:e4b"]["recommended_here"]
    assert not names["gemma4:e2b"]["installed"] and names["gemma4:e2b"]["size_gb"] == 4.6
    assert [m["name"] for m in data["other"]] == ["qwen3.5:4b"]


def test_saved_model_outside_the_lists_is_kept(monkeypatch):
    _serve(monkeypatch, TAGS)
    data = llm.build_model_list(LlmConfig(model="mistral:7b"), cuda=True, vram_gb=16)
    assert data["other"][0] == {"name": "mistral:7b", "size_gb": 0.0, "installed": False}


def test_unreachable_server_keeps_saved_value(monkeypatch):
    _serve(monkeypatch, urllib.error.URLError("down"))
    data = llm.build_model_list(LlmConfig(model="mistral:7b"), cuda=False, vram_gb=None)
    assert data["reachable"] is False
    assert data["other"][0]["name"] == "mistral:7b" and data["other"][0]["installed"] is None


@pytest.mark.parametrize(
    ("cuda", "vram", "expected"),
    [(False, None, "gemma4:e2b"), (True, 6, "gemma4:e2b"), (True, 12, "gemma4:e4b"), (True, 16, "gemma4:e4b")],
)
def test_recommendation_follows_hardware(cuda, vram, expected):
    assert llm.recommended_model(cuda, vram) == expected


def test_status_distinguishes_down_missing_ok(monkeypatch):
    _serve(monkeypatch, TAGS)
    assert llm.model_status(LlmConfig(model="gemma4:e4b")) == {"state": "ok"}
    assert llm.model_status(LlmConfig(model="gemma4:e2b")) == {"state": "missing"}
    _serve(monkeypatch, urllib.error.URLError("down"))
    assert llm.model_status(LlmConfig()) == {"state": "down"}


def _wait(pull, status):
    for _ in range(100):
        if pull.state()["status"] == status:
            return pull.state()
        time.sleep(0.02)
    raise AssertionError(pull.state())


def _lines(*events):
    return lambda url: b"".join(json.dumps(e).encode() + b"\n" for e in events)


def test_pull_success(monkeypatch):
    _serve(monkeypatch, _lines({"status": "pulling x", "total": 100, "completed": 50}, {"status": "success"}))
    pull = llm.ModelPull()
    assert pull.start(LlmConfig(), "gemma4:e2b")
    assert _wait(pull, "done")["model"] == "gemma4:e2b"


def test_pull_failure_reports_the_error(monkeypatch):
    _serve(monkeypatch, _lines({"error": "pull model manifest: file does not exist"}))
    pull = llm.ModelPull()
    pull.start(LlmConfig(), "nope:1b")
    assert "manifest" in _wait(pull, "error")["error"]


def test_pull_that_ends_early_is_an_error(monkeypatch):
    _serve(monkeypatch, _lines({"status": "pulling x", "total": 100, "completed": 10}))
    pull = llm.ModelPull()
    pull.start(LlmConfig(), "gemma4:e2b")
    _wait(pull, "error")


def test_pull_cancel(monkeypatch):
    pull = llm.ModelPull()

    class Slow(_Resp):
        def __iter__(self):
            yield b'{"status": "pulling x", "total": 100, "completed": 1}\n'
            while pull.state()["status"] == "pulling":
                time.sleep(0.01)
                if pull._cancel.is_set():
                    yield b'{"status": "pulling x", "total": 100, "completed": 2}\n'

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda r, timeout=None: Slow())
    pull.start(LlmConfig(), "gemma4:e2b")
    _wait(pull, "pulling")
    assert not pull.start(LlmConfig(), "other")  # one at a time
    pull.cancel()
    _wait(pull, "cancelled")


def test_dashboard_endpoints(flask_client, monkeypatch, patch_api):
    patch_api("cuda_available", lambda: False)
    _serve(monkeypatch, TAGS)
    models = flask_client.get("/api/settings/llm-models").get_json()
    assert models["selected"] and models["recommended"][0]["recommended_here"]
    status = flask_client.get("/api/settings/llm-status").get_json()
    assert status["reachable"] is True and "model_installed" in status


def test_loaded_model_reports_vram_and_ram(monkeypatch):
    ps = {"models": [{"name": "gemma4:e4b", "size": 8_000_000_000, "size_vram": 6_000_000_000}]}
    _serve(monkeypatch, ps)
    assert llm.loaded_model(LlmConfig(model="gemma4:e4b"), "gemma4:e4b") == {
        "plausible": True,
        "vram_gb": 6.0,
        "ram_gb": 2.0,
    }
    assert llm.loaded_model(LlmConfig(model="x"), "gemma4:e2b") is None


def test_loaded_model_none_when_server_down(monkeypatch):
    _serve(monkeypatch, urllib.error.URLError("down"))
    assert llm.loaded_model(LlmConfig(model="x"), "x") is None


def test_fully_offloaded_model_has_no_gpu_share(monkeypatch):
    ps = {"models": [{"name": "gemma4:e4b", "size": 7_000_000_000, "size_vram": 0}]}
    _serve(monkeypatch, ps)
    assert llm.loaded_model(LlmConfig(model="gemma4:e4b"), "gemma4:e4b") == {
        "plausible": True,
        "vram_gb": 0.0,
        "ram_gb": 7.0,
    }


def test_loaded_model_none_for_non_ollama_server(monkeypatch):
    _serve(monkeypatch, {"models": []})
    assert llm.loaded_model(LlmConfig(model="x", protocol="openai"), "x") is None


def test_under_reported_size_is_flagged_implausible(monkeypatch):
    # Ollama reports 0.22 GB for gemma4:e2b (4.6 GB on disk), all of it "in VRAM".
    ps = {"models": [{"name": "gemma4:e2b", "size": 220_000_000, "size_vram": 220_000_000}]}
    _serve(monkeypatch, ps)
    assert llm.loaded_model(LlmConfig(model="gemma4:e2b"), "gemma4:e2b", disk_gb=4.6) == {
        "plausible": False,
        "on_gpu": True,
    }


def test_size_near_the_disk_size_is_believed(monkeypatch):
    ps = {"models": [{"name": "gemma4:12b-it-qat", "size": 7_660_000_000, "size_vram": 7_660_000_000}]}
    _serve(monkeypatch, ps)
    loaded = llm.loaded_model(LlmConfig(model="gemma4:12b-it-qat"), "gemma4:12b-it-qat", disk_gb=7.2)
    assert loaded == {"plausible": True, "vram_gb": 7.7, "ram_gb": 0.0}


def _record_requests(monkeypatch, body):
    """`urlopen` that answers with *body* and keeps the requests it got."""
    seen = []

    def fake(request, timeout=None):
        seen.append(request)
        return _Resp(body(request.full_url) if callable(body) else json.dumps(body).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    return seen


def test_loaded_model_sends_the_api_key(monkeypatch):
    ps = {"models": [{"name": "m:1", "size": 2_000_000_000, "size_vram": 1_000_000_000}]}
    seen = _record_requests(monkeypatch, ps)
    assert llm.loaded_model(LlmConfig(model="m:1", api_key="secret"), "m:1") is not None
    assert seen[0].full_url.endswith("/api/ps")
    assert seen[0].get_header("Authorization") == "Bearer secret"


def test_pull_sends_the_api_key(monkeypatch):
    seen = _record_requests(monkeypatch, _lines({"status": "success"}))
    pull = llm.ModelPull()
    pull.start(LlmConfig(api_key="secret"), "gemma4:e2b")
    _wait(pull, "done")
    assert seen[0].get_header("Authorization") == "Bearer secret"
    assert seen[0].get_header("Content-type") == "application/json"


@pytest.mark.parametrize(
    "event",
    [[1, 2], {"status": "pulling x", "total": 100, "completed": None}, {"status": "pulling x", "total": "many"}],
)
def test_an_odd_pull_event_ends_the_pull_as_an_error_and_allows_the_next(monkeypatch, event):
    _serve(monkeypatch, _lines(event))
    pull = llm.ModelPull()
    pull.start(LlmConfig(), "gemma4:e2b")
    assert _wait(pull, "error")["error"]
    _serve(monkeypatch, _lines({"status": "success"}))
    assert pull.start(LlmConfig(), "gemma4:e2b")  # a dead thread must not block every later pull
    _wait(pull, "done")


def test_a_pull_widens_the_read_timeout_for_the_silent_digest_phase():
    class _Sock:
        timeout = None

        def settimeout(self, seconds):
            self.timeout = seconds

    sock = _Sock()
    response = type("R", (), {"fp": type("F", (), {"raw": type("W", (), {"_sock": sock})()})()})()
    llm._allow_silence(response, 900.0)
    assert sock.timeout == 900.0
    llm._allow_silence(object(), 900.0)  # a response without a socket is left alone


def test_a_read_timeout_ends_the_pull_as_an_error(monkeypatch):
    class Stalled(_Resp):
        def __iter__(self):
            raise TimeoutError("timed out")

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda r, timeout=None: Stalled())
    pull = llm.ModelPull()
    pull.start(LlmConfig(), "gemma4:e2b")
    assert "TimeoutError" in _wait(pull, "error")["error"]


def test_a_saved_model_without_a_tag_is_not_listed_twice(monkeypatch):
    _serve(monkeypatch, {"models": [{"name": "llama3:latest", "size": 4_000_000_000}]})
    data = llm.build_model_list(LlmConfig(model="llama3"), cuda=False, vram_gb=None)
    assert [m["name"] for m in data["other"]] == ["llama3:latest"]
    assert data["other"][0]["installed"] is True


def test_a_malformed_port_is_an_llm_error_not_a_crash(monkeypatch):
    def refuse(request, timeout=None):
        raise ValueError("Port could not be cast to integer value")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    with pytest.raises(llm.LlmError):
        llm.complete(LlmConfig(url="http://localhost:abc", protocol="openai"), "system", "user")


def test_a_nested_background_block_restores_the_outer_one():
    def background() -> bool:
        return getattr(llm._local, "background", False)

    assert background() is False
    with llm.background():
        with llm.background():
            assert background() is True
        assert background() is True
    assert background() is False


def test_wait_for_reload_returns_when_the_reload_ends_or_the_time_is_up(monkeypatch):
    config = LlmConfig(model="m", url="http://x:1")
    assert llm.wait_for_reload(config, 1.0) is True  # nothing reloading
    monkeypatch.setattr(llm, "_RELOAD_POLL_S", 0.01)
    with llm._reload_lock:
        llm._reloading.add(llm.model_key(config))
    try:
        assert llm.wait_for_reload(config, 0.05) is False
    finally:
        with llm._reload_lock:
            llm._reloading.discard(llm.model_key(config))


def test_listed_name_finds_the_latest_tag_of_an_untagged_model():
    have = [{"name": "llama3:latest"}, {"name": "gemma4:e2b"}]
    assert llm.listed_name("llama3", have) == "llama3:latest"
    assert llm.listed_name("gemma4:e2b", have) == "gemma4:e2b"
    assert llm.listed_name("gemma4", have) is None
    assert llm.listed_name("mistral:7b", have) is None


def test_the_dropdown_selects_the_listed_name_of_an_untagged_saved_model(db, session_factory, monkeypatch, patch_api):
    from nexusvox.config import Config
    from nexusvox.dashboard.api import DashboardAPI

    patch_api("cuda_available", lambda: False)
    _serve(monkeypatch, {"models": [{"name": "llama3:latest", "size": 4_000_000_000}]})
    api = DashboardAPI(session_factory, Config(llm=LlmConfig(model="llama3")), db=db)

    data = api.get_llm_models()

    assert [m["name"] for m in data["other"]] == ["llama3:latest"]
    assert data["selected"] == "llama3:latest"  # the name of an option, or the select shows nothing


def test_cancel_shuts_the_socket_down_so_a_waiting_read_ends(monkeypatch):
    shut = threading.Event()

    class _Sock:
        def settimeout(self, seconds):
            pass

        def shutdown(self, how):
            shut.set()

    class Silent(_Resp):
        fp = type("F", (), {"raw": type("W", (), {"_sock": _Sock()})()})()

        def __iter__(self):
            yield b'{"status": "verifying sha256 digest"}\n'
            if not shut.wait(5):  # a recv() that only the shutdown of the socket ends
                raise AssertionError("the read was never interrupted")
            raise OSError("connection shut down")

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda r, timeout=None: Silent())
    pull = llm.ModelPull()
    pull.start(LlmConfig(), "gemma4:e2b")
    for _ in range(100):
        if pull.state()["detail"] == "verifying sha256 digest":
            break
        time.sleep(0.02)
    pull.cancel()

    assert shut.is_set()
    _wait(pull, "cancelled")


def test_a_response_without_a_socket_logs_that_the_silent_phase_is_not_covered(caplog):
    with caplog.at_level("WARNING", logger="nexusvox.llm"):
        llm._allow_silence(object(), 900.0)
    assert "read timeout" in caplog.text
