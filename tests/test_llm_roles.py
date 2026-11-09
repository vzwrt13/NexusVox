"""A fast model and a main model for different tasks (`[llm].per_task`): mapping, moving a task, the switch,
warm-up, renewal and release of both models, and a release across servers and protocols."""

from __future__ import annotations

import dataclasses
import io
import json
import threading

import pytest

from nexusvox import llm
from nexusvox.config import Config, LlmConfig, LlmFastModel, LlmServer, load_config, save_config
from nexusvox.dictation.cleanup import CleanupResult
from nexusvox.dictation.pipeline import process_text

_real_note_pinned = llm._note_pinned  # conftest turns it off per test

OLLAMA = "http://127.0.0.1:11434"
LLAMA_SERVER = "http://127.0.0.1:8083/v1"


def two_models(**overrides) -> LlmConfig:
    """Main: a 12B model on an OpenAI-compatible llama-server; fast: a small model on Ollama."""
    values = {
        "url": LLAMA_SERVER,
        "protocol": "openai",
        "model": "gemma-12b.gguf",
        "per_task": True,
        "fast": LlmFastModel(OLLAMA, "ollama", "gemma4:e2b"),
        "servers": [LlmServer(LLAMA_SERVER, "openai", "llama-server")],
    }
    values.update(overrides)
    return LlmConfig(**values)


# -- the mapping ------------------------------------------------------------------------------------


def test_by_default_one_model_does_everything():
    config = LlmConfig()
    assert config.per_task is False and config.fast_tasks == ["dictation_cleanup"]
    for task in ("dictation_cleanup", "polish", "file_cleanup"):
        assert llm.config_for(config, task) is config
    assert llm.active_models(config) == [config]


def test_the_fast_model_cleans_dictations_and_the_main_model_does_the_rest():
    config = two_models()
    fast = llm.config_for(config, "dictation_cleanup")
    assert (fast.url, fast.protocol, fast.model) == (OLLAMA, "ollama", "gemma4:e2b")
    assert fast.timeout_s == config.timeout_s and fast.keep_alive == config.keep_alive  # shared settings
    assert llm.config_for(config, "polish") is config
    assert llm.config_for(config, "file_cleanup") is config


def test_a_task_moved_to_the_fast_model_goes_there():
    config = two_models(fast_tasks=["dictation_cleanup", "polish"])
    assert llm.config_for(config, "polish").model == "gemma4:e2b"
    config.fast_tasks = []  # dictation cleanup moved to the main model
    assert llm.config_for(config, "dictation_cleanup") is config


def test_switched_off_the_main_model_does_everything_again():
    config = two_models(per_task=False)
    assert llm.config_for(config, "dictation_cleanup") is config
    assert llm.active_models(config) == [config]


def test_with_no_fast_model_picked_its_tasks_use_the_main_model():
    config = two_models(fast=LlmFastModel())
    assert llm.config_for(config, "dictation_cleanup") is config


def test_the_active_models_are_the_main_model_and_a_fast_model_with_a_task():
    config = two_models()
    assert [m.model for m in llm.active_models(config)] == ["gemma-12b.gguf", "gemma4:e2b"]
    config.fast_tasks = []  # every task on the main model: the fast one has nothing to do
    assert [m.model for m in llm.active_models(config)] == ["gemma-12b.gguf"]
    same = two_models(fast=LlmFastModel(LLAMA_SERVER, "openai", "gemma-12b.gguf"))
    assert len(llm.active_models(same)) == 1  # the same model twice is one model


def test_dictation_cleanup_in_the_pipeline_asks_the_fast_model():
    config = Config()
    config.llm = two_models()
    config.cleanup.level = "light"
    asked = []

    def cleanup_fn(text, cleanup, model):
        asked.append(model)
        return CleanupResult(text)

    process_text("one two three four five six", config, [], cleanup_fn=cleanup_fn)
    assert [m.model for m in asked] == ["gemma4:e2b"]


# -- the config file --------------------------------------------------------------------------------


def test_an_old_config_without_the_new_keys_loads_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[llm]\nurl = "http://127.0.0.1:11434"\nprotocol = "ollama"\nmodel = "gemma4:e4b"\n', encoding="utf-8"
    )
    config = load_config().llm
    assert (config.model, config.per_task, config.fast, config.fast_tasks) == (
        "gemma4:e4b",
        False,
        LlmFastModel(),
        ["dictation_cleanup"],
    )


def test_both_models_and_the_task_list_survive_a_save(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config()
    config.llm = two_models(fast_tasks=["polish", "dictation_cleanup"])
    save_config(config)
    loaded = load_config().llm
    assert loaded.per_task is True
    assert loaded.fast == LlmFastModel(OLLAMA, "ollama", "gemma4:e2b")
    assert loaded.fast_tasks == ["dictation_cleanup", "polish"]  # in the order of LLM_TASKS
    assert (loaded.url, loaded.model) == (LLAMA_SERVER, "gemma-12b.gguf")


def test_unknown_tasks_and_a_broken_fast_table_are_dropped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        '[llm]\nper_task = true\nfast_tasks = ["polish", "dance"]\nfast = "not a table"\n', encoding="utf-8"
    )
    config = load_config().llm
    assert config.fast_tasks == ["polish"] and config.fast == LlmFastModel()


def test_the_example_config_documents_the_new_keys():
    from nexusvox.config import EXAMPLE_CONFIG_PATH

    text = EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
    for key in ("per_task", "fast_tasks", "[llm.fast]"):
        assert key in text


# -- warm-up, renewal and release -------------------------------------------------------------------


class Server:
    """Stands in for `urlopen`: `/api/ps` lists `loaded`, every POST is recorded as (url, body)."""

    def __init__(self, loaded=()):
        self.loaded, self.posts = set(loaded), []

    def __call__(self, request, timeout=None):
        if isinstance(request, str) or request.full_url.endswith("/api/ps"):
            models = [{"name": m, "size": 5, "size_vram": 5} for m in sorted(self.loaded)]
            return io.BytesIO(json.dumps({"models": models}).encode())
        self.posts.append((request.full_url, json.loads(request.data)))
        return io.BytesIO(b'{"message": {"content": "OK"}, "choices": [{"message": {"content": "OK"}}]}')


@pytest.fixture
def server(monkeypatch):
    llm._pinned.clear()
    llm._reloading.clear()
    llm._release_after_load.clear()
    llm._in_flight.clear()
    monkeypatch.setattr(llm, "_note_pinned", _real_note_pinned)
    fake = Server()
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    yield fake
    llm._pinned.clear()
    llm._reloading.clear()
    llm._release_after_load.clear()
    llm._in_flight.clear()


def _released(server, model):
    return any(body == {"model": model, "keep_alive": 0} for _, body in server.posts)


def test_the_renewal_keeps_both_models_loaded_and_no_other(server):
    config = two_models(url=OLLAMA, protocol="ollama", model="gemma4:12b")
    for model in ("gemma4:12b", "gemma4:e2b", "old:1b"):
        llm._pinned[(OLLAMA, model)] = True
        server.loaded.add(model)
    assert llm.renew_once(config) == 2
    renewed = [body["model"] for url, body in server.posts if url.endswith("/api/generate")]
    assert renewed == ["gemma4:12b", "gemma4:e2b"]  # the model no longer in use expires on its own


def test_the_renewal_keeps_the_fast_ollama_model_when_the_main_model_is_not_on_ollama(server):
    llm._pinned[(OLLAMA, "gemma4:e2b")] = True
    server.loaded.add("gemma4:e2b")
    assert llm.renew_once(two_models()) == 1


def test_quit_releases_both_models(server, monkeypatch):
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: None)
    config = two_models(url=OLLAMA, protocol="ollama", model="gemma4:12b")
    for model in llm.active_models(config):
        llm.complete(model, "system", "user")
    llm.unload_all(config)
    assert _released(server, "gemma4:12b") and _released(server, "gemma4:e2b")
    assert not llm._pinned


def test_a_release_during_a_load_is_repeated_when_the_load_ends(server, monkeypatch):
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: None)
    config = LlmConfig(model="gemma4:e2b")
    gate, started = threading.Event(), threading.Event()
    real = server.__call__

    def slow(request, timeout=None):
        if getattr(request, "data", None) and "messages" in json.loads(request.data):
            started.set()
            gate.wait(5)  # the load is still running
        return real(request, timeout)

    monkeypatch.setattr(llm.urllib.request, "urlopen", slow)
    assert llm.reload_in_background(config, reason="warm-up")
    assert started.wait(5)
    assert llm.unload(config) is True  # the user switched to another model meanwhile
    gate.set()
    for thread in threading.enumerate():
        if thread.name == "llm-reload":
            thread.join(5)
    releases = [body for _, body in server.posts if body == {"model": "gemma4:e2b", "keep_alive": 0}]
    assert len(releases) == 2  # the load pinned it again; it is released once more
    assert not llm._pinned


# -- the dashboard ----------------------------------------------------------------------------------


@pytest.fixture
def settings(db, session_factory, monkeypatch, no_config_save):
    """The settings API on a config with two servers saved, warm-up and release recorded."""
    import nexusvox.dashboard.api_settings as api_module
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.llm = LlmConfig(url=OLLAMA, model="gemma4:e2b", servers=[LlmServer(LLAMA_SERVER, "openai", "llama")])
    config.cleanup.level = "light"
    api = DashboardAPI(session_factory, config, db=db)
    api.warmed, api.released = [], []
    monkeypatch.setattr(api_module, "llm_warm_up", lambda cfg: api.warmed.append((cfg.url, cfg.model)))
    monkeypatch.setattr(api, "_unload_in_background", lambda cfg: api.released.append((cfg.url, cfg.model)))
    return api


def test_switching_the_main_model_to_another_server_releases_the_old_ollama_model(settings):
    settings.select_llm_server_model(LLAMA_SERVER, "gemma-12b.gguf")
    assert settings.released == [(OLLAMA, "gemma4:e2b")]
    assert settings.warmed == [(LLAMA_SERVER, "gemma-12b.gguf")]
    llm_config = settings._config.llm
    assert (llm_config.url, llm_config.protocol, llm_config.model) == (LLAMA_SERVER, "openai", "gemma-12b.gguf")


def test_the_cross_server_release_reaches_ollama(server, monkeypatch, db, session_factory, no_config_save):
    """End to end: Ollama's model was pinned by a request; picking a llama-server model unloads it there."""
    import nexusvox.dashboard.api_settings as api_module
    from nexusvox.dashboard.api import DashboardAPI

    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: None)
    monkeypatch.setattr(api_module, "llm_warm_up", lambda cfg: None)
    done = threading.Event()
    monkeypatch.setattr(api_module, "llm_unload", lambda cfg: (llm.unload(cfg), done.set()))
    config = Config()
    config.llm = LlmConfig(url=OLLAMA, model="gemma4:e2b", servers=[LlmServer(LLAMA_SERVER, "openai", "llama")])
    llm.complete(config.llm, "system", "user")
    DashboardAPI(session_factory, config, db=db).select_llm_server_model(LLAMA_SERVER, "gemma-12b.gguf")
    assert done.wait(5)
    assert (f"{OLLAMA}/api/chat", {"model": "gemma4:e2b", "keep_alive": 0}) in server.posts


def test_switching_per_task_on_warms_the_fast_model_and_off_releases_it(settings):
    settings.select_llm_server_model(LLAMA_SERVER, "gemma-12b.gguf", "fast")
    assert settings.warmed == [] and settings.released == []  # picked, but not in use while the switch is off
    data = settings.set_llm_per_task(True)
    assert data["roles"]["enabled"] is True
    assert data["roles"]["fast"] == {
        "url": LLAMA_SERVER,
        "protocol": "openai",
        "model": "gemma-12b.gguf",
        "server": "llama",
    }
    assert settings.warmed == [(LLAMA_SERVER, "gemma-12b.gguf")]
    settings.set_llm_per_task(False)
    assert settings.released == [(LLAMA_SERVER, "gemma-12b.gguf")]


def test_switching_a_role_releases_only_that_roles_old_model(settings):
    settings._config.llm.per_task = True
    settings.select_llm_server_model(OLLAMA, "gemma4:1b", "fast")
    settings.warmed.clear()
    settings.select_llm_server_model(LLAMA_SERVER, "gemma-12b.gguf", "fast")
    assert settings.released == [(OLLAMA, "gemma4:1b")]  # the main model stays loaded
    assert settings.warmed == [(LLAMA_SERVER, "gemma-12b.gguf")]
    settings.set_llm_model("gemma4:e4b")  # the main model on Ollama
    assert settings.released[-1] == (OLLAMA, "gemma4:e2b")


def test_a_model_both_roles_used_is_not_released_while_one_still_uses_it(settings):
    settings._config.llm.per_task = True
    settings.select_llm_server_model(OLLAMA, "gemma4:e2b", "fast")  # the same model as the main one
    settings.select_llm_server_model(LLAMA_SERVER, "gemma-12b.gguf")  # the main model moves away
    assert settings.released == []  # gemma4:e2b still cleans dictations


def test_the_fast_model_can_come_from_the_unsaved_server_of_the_main_model(settings):
    settings._config.llm.servers = []
    settings._config.llm.url, settings._config.llm.protocol = LLAMA_SERVER, "openai"
    assert "error" not in settings.select_llm_server_model(LLAMA_SERVER, "small.gguf", "fast")
    assert settings._config.llm.fast == LlmFastModel(LLAMA_SERVER, "openai", "small.gguf")
    assert "error" in settings.select_llm_server_model("http://127.0.0.1:9999", "x", "fast")
    assert "error" in settings.select_llm_server_model(OLLAMA, "x", "slow")


def test_moving_a_task_between_the_roles(settings):
    settings._config.llm.per_task = True
    settings.select_llm_server_model(LLAMA_SERVER, "gemma-12b.gguf", "fast")
    data = settings.set_llm_task_role("polish", "fast")
    assert {t["id"]: t["role"] for t in data["roles"]["tasks"]} == {
        "dictation_cleanup": "fast",
        "polish": "fast",
        "file_cleanup": "main",
    }
    assert llm.config_for(settings._config.llm, "polish").model == "gemma-12b.gguf"
    settings.set_llm_task_role("polish", "main")
    settings.set_llm_task_role("dictation_cleanup", "main")
    assert settings._config.llm.fast_tasks == []
    assert settings.released == [(LLAMA_SERVER, "gemma-12b.gguf")]  # nothing left for the fast model
    assert "error" in settings.set_llm_task_role("dance", "fast")
    assert "error" in settings.set_llm_task_role("polish", "slow")


def test_the_routes(flask_client, monkeypatch):
    import nexusvox.dashboard.api_settings as api_module

    monkeypatch.setattr(api_module, "llm_warm_up", lambda cfg: None)
    monkeypatch.setattr(
        api_module,
        "llm_build_model_list",
        lambda *a: {"reachable": False, "recommended": [], "other": [], "protocol": "ollama"},
    )
    monkeypatch.setattr(api_module, "server_models", lambda *a, **k: {"reachable": False, "models": []})

    def post(path, body):
        return flask_client.post(path, data=json.dumps(body), content_type="application/json")

    assert post("/api/settings/llm-per-task", {"enabled": True}).get_json()["roles"]["enabled"] is True
    assert post("/api/settings/llm-per-task", {"enabled": "yes"}).status_code == 400
    moved = post("/api/settings/llm-task-role", {"task": "file_cleanup", "role": "fast"}).get_json()
    assert {t["id"]: t["role"] for t in moved["roles"]["tasks"]}["file_cleanup"] == "fast"
    assert post("/api/settings/llm-task-role", {"task": "x", "role": "fast"}).status_code == 400
    picked = post("/api/settings/llm-select", {"url": OLLAMA, "model": "gemma4:e2b", "role": "fast"})
    assert picked.status_code == 200
    roles = flask_client.get("/api/settings/llm-models").get_json()["roles"]
    assert roles["fast"]["model"] == "gemma4:e2b" and roles["fast"]["server"] == "Ollama"


# -- review follow-ups ------------------------------------------------------------------------------


def _join_loads():
    for thread in threading.enumerate():
        if thread.name == "llm-reload":
            thread.join(5)


def _slow_chat(server, monkeypatch):
    """`urlopen` whose chat requests wait for the returned gate; `started` is set when one arrives."""
    gate, started = threading.Event(), threading.Event()
    real = server.__call__

    def slow(request, timeout=None):
        if getattr(request, "data", None) and "messages" in json.loads(request.data):
            started.set()
            gate.wait(5)
        return real(request, timeout)

    monkeypatch.setattr(llm.urllib.request, "urlopen", slow)
    return gate, started


def test_a_model_back_in_use_before_its_load_ends_is_not_released(server, monkeypatch):
    """Per-task models on, off, on again while the fast model still loads: it stays loaded and noted."""
    monkeypatch.setattr(llm, "loaded_model", lambda cfg, name: None)
    config = LlmConfig(model="gemma4:e2b")
    gate, started = _slow_chat(server, monkeypatch)
    assert llm.reload_in_background(config, reason="warm-up")
    assert started.wait(5)
    llm.unload(config)  # switched off: released now, and queued for after the load
    assert llm.reload_in_background(config, reason="warm-up") is False  # switched on again: the load still runs
    gate.set()
    _join_loads()
    releases = [body for _, body in server.posts if body == {"model": "gemma4:e2b", "keep_alive": 0}]
    assert len(releases) == 1  # the queued one was dropped
    assert llm.model_key(config) in llm._pinned


def test_the_dashboard_drops_a_queued_release_of_a_model_back_in_use(settings, monkeypatch):
    """Main model A -> B -> A with cleanup off (no warm-up): A's release queued during its load is dropped."""
    a = dataclasses.replace(settings._config.llm)
    llm._release_after_load.add(llm.model_key(a))
    settings.set_llm_model("gemma4:e4b")
    settings.set_llm_model("gemma4:e2b")
    assert llm.model_key(a) not in llm._release_after_load


def test_a_release_during_the_probe_before_a_load_is_sent_after_the_load(server, monkeypatch):
    """A release while the load still asks whether the model is loaded (not noted yet) is not lost."""
    probing, go = threading.Event(), threading.Event()

    def slow_probe(cfg, name):
        probing.set()
        go.wait(5)

    monkeypatch.setattr(llm, "loaded_model", slow_probe)
    config = LlmConfig(model="gemma4:e2b")
    assert llm.reload_in_background(config, reason="warm-up")
    assert probing.wait(5)
    assert llm.unload(config) is False  # nothing to release yet, but queued
    go.set()
    _join_loads()
    assert (f"{OLLAMA}/api/chat", {"model": "gemma4:e2b", "keep_alive": 0}) in server.posts
    assert not llm._pinned and not llm._release_after_load and not llm._in_flight


def test_turning_cleanup_on_warms_the_fast_model_too(settings):
    settings._config.llm.per_task = True
    settings.select_llm_server_model(LLAMA_SERVER, "small.gguf", "fast")
    settings.set_cleanup_level("off")
    settings.warmed.clear()
    settings.set_cleanup_level("light")
    assert settings.warmed == [(OLLAMA, "gemma4:e2b"), (LLAMA_SERVER, "small.gguf")]


def test_picking_the_same_model_again_warms_it_up(settings):
    settings.set_llm_model("gemma4:e2b")  # e.g. after Ollama restarted and dropped it
    assert settings.warmed == [(OLLAMA, "gemma4:e2b")] and settings.released == []
    settings._config.llm.per_task = True
    settings.select_llm_server_model(LLAMA_SERVER, "small.gguf", "fast")
    settings.warmed.clear()
    settings.select_llm_server_model(LLAMA_SERVER, "small.gguf", "fast")
    assert settings.warmed == [(LLAMA_SERVER, "small.gguf")]  # the main model is not touched


def test_a_fast_model_from_an_unsaved_server_stays_selectable_after_the_main_model_moves(settings):
    unsaved = "http://10.0.0.5:8080/v1"
    llm_config = settings._config.llm
    llm_config.per_task = True
    llm_config.url, llm_config.protocol, llm_config.api_key = unsaved, "openai", "secret"
    assert "error" not in settings.select_llm_server_model(unsaved, "small.gguf", "fast")
    assert "error" not in settings.select_llm_server_model(OLLAMA, "gemma4:e4b")  # the main model moves away
    picked = settings.select_llm_server_model(unsaved, "other.gguf", "fast")
    assert "error" not in picked
    assert llm_config.fast == LlmFastModel(unsaved, "openai", "other.gguf", "secret")
    llm_config.per_task = False  # not in use: its key is still known for a release at quit
    assert llm._api_key_for(llm_config, unsaved) == "secret"


def test_per_task_is_on_only_for_a_real_true(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for raw, expected in (('"false"', False), ('"true"', False), ("1", False), ("true", True)):
        (tmp_path / "config.toml").write_text(f"[llm]\nper_task = {raw}\n", encoding="utf-8")
        assert load_config().llm.per_task is expected, raw


def test_servers_are_named_by_one_rule(settings, monkeypatch):
    import nexusvox.dashboard.api_settings as api_module

    monkeypatch.setattr(api_module, "server_models", lambda *a, **k: {"reachable": False, "models": []})
    settings._config.llm.per_task = True
    settings.select_llm_server_model(LLAMA_SERVER, "small.gguf")
    _, servers = settings._llm_server_lists()
    roles = settings._llm_roles()
    assert roles["main_server"] == servers[0]["name"] == "llama"
    settings._config.llm.servers[0].name = ""
    assert settings._llm_roles()["main_server"] == settings._llm_server_lists()[1][0]["name"] == "127.0.0.1:8083"
