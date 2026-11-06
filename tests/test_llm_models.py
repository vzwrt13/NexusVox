"""The LLM dropdown's data: Ollama's model list, the model-aware status and the model pull."""

from __future__ import annotations

import io
import json
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
