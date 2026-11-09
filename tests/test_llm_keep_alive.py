"""The language model stays loaded while the app runs and is released on quit or a model switch."""

from __future__ import annotations

import json
import threading
import time

import pytest

from nexusvox import llm
from nexusvox.config import Config, LlmConfig, load_config, save_config

_real_note_pinned = llm._note_pinned


class Recorder:
    """Stands in for `urlopen` of `llm._post`: records the requests, answers with an empty JSON object."""

    def __init__(self):
        self.bodies: list[dict] = []

    def __call__(self, request, timeout=None):
        import io

        self.bodies.append(json.loads(request.data))
        return io.BytesIO(b'{"message": {"content": "OK"}, "choices": [{"message": {"content": "OK"}}]}')


@pytest.fixture
def server(monkeypatch):
    llm._pinned.clear()
    llm._reloading.clear()
    monkeypatch.setattr(llm, "_note_pinned", _real_note_pinned)  # conftest turns it off
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: None)  # Ollama holds nothing yet
    recorder = Recorder()
    monkeypatch.setattr(llm.urllib.request, "urlopen", recorder)
    yield recorder
    llm._pinned.clear()
    llm._reloading.clear()


def _request(monkeypatch, config: LlmConfig, already_loaded: bool = False) -> None:
    """One request to the model; Ollama either holds the model already or not."""
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: {"plausible": True} if already_loaded else None)
    llm.complete(config, "system", "user")


def test_the_default_is_an_hour_that_the_app_renews(server):
    assert LlmConfig().keep_alive == "60m"
    llm.complete(LlmConfig(), "system", "user")
    assert server.bodies[0]["keep_alive"] == "60m"


def test_a_never_expire_value_goes_out_as_a_number(server):
    llm.complete(LlmConfig(keep_alive="-1"), "system", "user")
    assert server.bodies[0]["keep_alive"] == -1  # a string "-1" is rejected by Ollama


def test_a_user_set_duration_is_sent_unchanged(server):
    llm.complete(LlmConfig(keep_alive="10m"), "system", "user")
    assert server.bodies[0]["keep_alive"] == "10m"


@pytest.mark.parametrize(
    ("saved", "loaded"),
    [
        ('keep_alive = "10m"', "60m"),  # the old default, saved by every earlier version
        ('keep_alive = "30m"', "30m"),
        ('keep_alive = "24h"', "24h"),
        ("keep_alive = 0", "0"),
        ('keep_alive = "-1"', "-1"),  # the user's own, stays
        ("", "60m"),  # key missing
    ],
)
def test_the_old_default_migrates_and_a_chosen_value_stays(tmp_path, monkeypatch, saved, loaded):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("[llm]" + chr(10) + saved + chr(10), encoding="utf-8")
    config = load_config()
    assert config.llm.keep_alive == loaded
    save_config(config)  # written back on the next save
    assert load_config().llm.keep_alive == loaded
    assert (f'keep_alive = "{loaded}"') in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_a_fresh_config_uses_the_new_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_config(Config())
    assert load_config().llm.keep_alive == "60m"


def test_quit_unloads_a_model_the_app_loaded_whatever_loaded_it(server, monkeypatch):
    config = LlmConfig()
    _request(monkeypatch, config)  # e.g. polish with cleanup off: no warm-up involved
    assert llm.unload(config) is True
    assert server.bodies[-1] == {"model": config.model, "keep_alive": 0}
    assert llm.unload(config) is False  # only once


def test_a_model_that_was_loaded_before_the_app_goes_back_to_ollamas_own_expiry(server, monkeypatch):
    config = LlmConfig()
    _request(monkeypatch, config, already_loaded=True)
    assert llm.unload(config) is True
    assert server.bodies[-1] == {"model": config.model, "keep_alive": "5m"}  # not unloaded, not pinned


def test_quit_releases_every_model_the_app_pinned_not_only_the_current_one(server, monkeypatch):
    _request(monkeypatch, LlmConfig(model="a:1b"))
    _request(monkeypatch, LlmConfig(model="b:1b"))
    llm.unload_all(LlmConfig(model="b:1b"))
    assert {"model": "a:1b", "keep_alive": 0} in server.bodies and {"model": "b:1b", "keep_alive": 0} in server.bodies
    assert not llm._pinned


def test_a_model_still_loading_is_noted_before_the_load_ends(server, monkeypatch):
    config = LlmConfig()
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: None)
    seen = []

    def during_load(request, timeout=None):
        if not seen:
            seen.append(None)
            seen[0] = llm.unload(config)  # quit arrives while the first request is still running
        return server(request, timeout)

    monkeypatch.setattr(llm.urllib.request, "urlopen", during_load)
    llm.complete(config, "system", "user")
    assert seen == [True]


def test_a_failed_release_is_kept_for_the_next_try(server, monkeypatch):
    config = LlmConfig()
    _request(monkeypatch, config)

    def refuse(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    assert llm.unload(config) is False
    assert llm.model_key(config) in llm._pinned


def test_nothing_is_unloaded_for_other_protocols(server):
    config = LlmConfig(protocol="openai")
    llm.complete(config, "system", "user")  # the server fake answers in Ollama's shape; only the body matters
    assert llm.unload(config) is False
    assert not llm._pinned


def test_unload_now_notes_nothing(server):
    llm.complete(LlmConfig(keep_alive="0"), "system", "user")
    assert not llm._pinned


@pytest.mark.parametrize(("value", "sent"), [("-1", -1), ("-1.0", -1), ("300.0", 300), ("1.5", 1.5), ("5m", "5m")])
def test_a_numeric_keep_alive_goes_out_as_a_number(value, sent):
    assert llm._keep_alive_value(value) == sent


def test_an_untagged_name_matches_ollamas_latest_tag(monkeypatch):
    import io

    reply = json.dumps({"models": [{"name": "llama3:latest", "size": 5, "size_vram": 5}]}).encode()
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(reply))
    assert llm.loaded_model(LlmConfig(model="llama3"), "llama3") is not None
    assert llm.loaded_model(LlmConfig(model="llama3"), "llama2") is None


def _switch(flask_client, server, monkeypatch, path, payload):
    import nexusvox.dashboard.api_settings as api_module

    old = flask_client.get("/api/settings").get_json()["llm_model"]
    _request(monkeypatch, LlmConfig(model=old))
    monkeypatch.setattr(api_module, "llm_warm_up", lambda cfg: None)
    unloaded = threading.Event()
    real_unload = llm.unload

    def spy(cfg):
        result = real_unload(cfg)
        unloaded.set()
        return result

    monkeypatch.setattr(api_module, "llm_unload", spy)
    flask_client.post(path, data=json.dumps(payload), content_type="application/json")
    assert unloaded.wait(5)
    assert {"model": old, "keep_alive": 0} in server.bodies


def test_switching_the_model_releases_the_old_one(flask_client, server, monkeypatch):
    _switch(flask_client, server, monkeypatch, "/api/settings/llm-model", {"model": "other:1b"})


def test_picking_a_model_in_the_grouped_dropdown_releases_the_old_one(flask_client, server, monkeypatch):
    payload = {"url": "http://127.0.0.1:11434", "model": "other:1b"}
    _switch(flask_client, server, monkeypatch, "/api/settings/llm-select", payload)


# -- the renewal timer ------------------------------------------------------------------------------


class Ollama:
    """A fake Ollama: /api/ps lists `loaded`, every POST is recorded as (path, body)."""

    def __init__(self, loaded: set[str]):
        self.loaded, self.posts = loaded, []

    def __call__(self, request, timeout=None):
        import io

        if isinstance(request, str) or request.full_url.endswith("/api/ps"):
            models = [{"name": m, "size": 5, "size_vram": 5} for m in sorted(self.loaded)]
            return io.BytesIO(json.dumps({"models": models}).encode())
        self.posts.append((request.full_url.rsplit("/api/", 1)[1], json.loads(request.data)))
        return io.BytesIO(b'{"message": {"content": "OK"}}')


@pytest.fixture
def ollama(monkeypatch):
    llm._pinned.clear()
    fake = Ollama({"a:1b"})
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    yield fake
    llm._pinned.clear()
    llm.stop_renewal()


def test_the_timer_renews_a_model_that_is_still_loaded(ollama, caplog):
    llm._pinned[("http://127.0.0.1:11434", "a:1b")] = True
    with caplog.at_level("INFO", logger="nexusvox.llm"):
        assert llm.renew_once(LlmConfig(model="a:1b")) == 1
    assert [r.getMessage() for r in caplog.records] == ["Language model a:1b kept loaded for 60m"]
    assert ollama.posts == [("generate", {"model": "a:1b", "keep_alive": "60m"})]  # load-only: no prompt


def test_the_timer_never_loads_a_model_ollama_dropped(ollama, caplog):
    llm._pinned[("http://127.0.0.1:11434", "gone:1b")] = True
    with caplog.at_level("INFO", logger="nexusvox.llm"):
        assert llm.renew_once(LlmConfig(model="gone:1b")) == 0
    assert [r.getMessage() for r in caplog.records] == ["Language model gone:1b not loaded, renewal skipped"]
    assert ollama.posts == []


def test_the_timer_renews_with_a_custom_value(ollama):
    llm._pinned[("http://127.0.0.1:11434", "a:1b")] = True
    llm.renew_once(LlmConfig(model="a:1b", keep_alive="30m"))
    assert ollama.posts[0][1]["keep_alive"] == "30m"


@pytest.mark.parametrize("value", ["-1", "0", "forever"])
def test_the_timer_skips_a_never_or_now_value(ollama, value):
    llm._pinned[("http://127.0.0.1:11434", "a:1b")] = True
    assert llm.renew_once(LlmConfig(model="a:1b", keep_alive=value)) == 0
    assert ollama.posts == []


def test_only_the_ollama_protocol_is_renewed(ollama):
    llm._pinned[("http://127.0.0.1:11434", "a:1b")] = True
    assert llm.renew_once(LlmConfig(model="a:1b", protocol="openai")) == 0


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("60m", 3600), ("1h30m", 5400), ("90s", 90), ("300", 300), ("-1", None), ("0", None), ("x", None)],
)
def test_keep_alive_durations(value, seconds):
    assert llm._keep_alive_seconds(value) == seconds


def test_the_renewal_runs_every_fifty_minutes_and_stops_on_quit(ollama, monkeypatch):
    assert llm._renew_interval(LlmConfig()) == 3000.0
    assert llm._renew_interval(LlmConfig(keep_alive="-1")) is None
    llm._pinned[("http://127.0.0.1:11434", "a:1b")] = True
    config = LlmConfig(model="a:1b", keep_alive="1m")
    monkeypatch.setattr(llm, "_MIN_RENEW_INTERVAL_S", 0.05)
    monkeypatch.setattr(llm, "_RENEW_SHARE", 0.001)  # a round every ~0.06 s
    llm.start_renewal(lambda: config)
    deadline = time.monotonic() + 5
    while not ollama.posts and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ollama.posts
    llm.stop_renewal()
    assert not llm._renew_thread.is_alive()
    count = len(ollama.posts)
    time.sleep(0.3)
    assert len(ollama.posts) == count  # quit stopped the timer
