"""The language model stays loaded while the app runs and is released on quit or a model switch."""

from __future__ import annotations

import json
import threading

import pytest

from nexusvox import llm
from nexusvox.config import Config, LlmConfig, load_config, save_config


class Recorder:
    """Stands in for `urlopen` of `llm._post`: records the requests, answers with an empty JSON object."""

    def __init__(self):
        self.bodies: list[dict] = []

    def __call__(self, request, timeout=None):
        import io

        self.bodies.append(json.loads(request.data))
        return io.BytesIO(b'{"message": {"content": "OK"}}')


@pytest.fixture
def server(monkeypatch):
    llm._warmed.clear()
    llm._reloading.clear()
    recorder = Recorder()
    monkeypatch.setattr(llm.urllib.request, "urlopen", recorder)
    yield recorder
    llm._warmed.clear()
    llm._reloading.clear()


def _warm(monkeypatch, config: LlmConfig, already_loaded: bool = False) -> None:
    """Run `warm_up` to the end; Ollama either holds the model already or not."""
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: {"plausible": True} if already_loaded else None)
    llm.warm_up(config)
    for t in threading.enumerate():
        if t.name == "llm-reload":
            t.join(5)


def test_the_default_keeps_the_model_loaded_and_goes_out_as_a_number(server):
    assert LlmConfig().keep_alive == "-1"
    llm.complete(LlmConfig(), "system", "user")
    assert server.bodies[0]["keep_alive"] == -1  # a string "-1" is rejected by Ollama


def test_a_user_set_duration_is_sent_unchanged(server):
    llm.complete(LlmConfig(keep_alive="10m"), "system", "user")
    assert server.bodies[0]["keep_alive"] == "10m"


def test_an_old_config_keeps_its_value_and_a_missing_key_gets_the_new_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[llm]\nkeep_alive = "10m"\n', encoding="utf-8")
    assert load_config().llm.keep_alive == "10m"
    (tmp_path / "config.toml").write_text("[llm]\nmodel = 'x'\n", encoding="utf-8")
    assert load_config().llm.keep_alive == "-1"
    save_config(Config())
    assert load_config().llm.keep_alive == "-1"


def test_quit_unloads_the_model_the_app_warmed_up(server, monkeypatch):
    config = LlmConfig()
    _warm(monkeypatch, config)
    assert llm.unload(config) is True
    assert server.bodies[-1] == {"model": config.model, "keep_alive": 0}
    assert llm.unload(config) is False  # only once


def test_a_model_that_was_loaded_before_the_app_started_stays(server, monkeypatch):
    config = LlmConfig()
    _warm(monkeypatch, config, already_loaded=True)
    sent = len(server.bodies)
    assert llm.unload(config) is False
    assert len(server.bodies) == sent


def test_nothing_is_unloaded_for_other_protocols(server):
    config = LlmConfig(protocol="openai")
    llm._warmed.add(llm._reload_key(config))
    assert llm.unload(config) is False
    assert server.bodies == []


def test_a_failing_unload_does_not_raise(server, monkeypatch):
    config = LlmConfig()
    _warm(monkeypatch, config)

    def refuse(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    assert llm.unload(config) is False


def test_switching_the_model_releases_the_old_one(flask_client, server, monkeypatch):
    import nexusvox.dashboard.api_settings as api_module

    old = flask_client.get("/api/settings").get_json()["llm_model"]
    config = LlmConfig(model=old)
    _warm(monkeypatch, config)
    monkeypatch.setattr(api_module, "llm_warm_up", lambda cfg: None)
    unloaded = threading.Event()
    real_unload = llm.unload

    def spy(cfg):
        result = real_unload(cfg)
        unloaded.set()
        return result

    monkeypatch.setattr(api_module, "llm_unload", spy)
    body = json.dumps({"model": "other:1b"})
    flask_client.post("/api/settings/llm-model", data=body, content_type="application/json")
    assert unloaded.wait(5)
    assert {"model": old, "keep_alive": 0} in server.bodies
