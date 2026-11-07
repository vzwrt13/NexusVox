"""Saved custom language model servers: detection, model lists, the config round trip, the dropdown data,
the request for no thinking and the hard deadline of a slow request."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import time
import urllib.error

import pytest
from dashboard_js import dashboard_source

from nexusvox import llm
from nexusvox.config import Config, LlmConfig, LlmServer, load_config, save_config
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.llm import LlmError, complete, detect_server, server_models

TAGS = {"models": [{"name": "gemma4:e4b", "size": 6_600_000_000}]}
OPENAI_MODELS = {"object": "list", "data": [{"id": "qwen-27b"}, {"id": "other"}]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))  # type: ignore[arg-type]


def _serve(monkeypatch, routes: dict):
    """urlopen that answers by URL: a dict is the JSON body, an Exception is raised, a number is an HTTP error.
    A URL that is not listed is refused like a closed port."""
    seen: list[str] = []

    def fake(request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        seen.append(url)
        answer = routes.get(url, urllib.error.URLError(ConnectionRefusedError(10061, "refused")))
        if isinstance(answer, int):
            raise _http_error(url, answer)
        if isinstance(answer, Exception):
            raise answer
        return _Resp(json.dumps(answer).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    return seen


# ── detection ─────────────────────────────────────────────────────────────────────────────────────
def test_detects_ollama_by_its_tags(monkeypatch):
    _serve(monkeypatch, {"http://127.0.0.1:11434/api/tags": TAGS})
    server = detect_server("http://127.0.0.1:11434/")
    assert (server.url, server.protocol) == ("http://127.0.0.1:11434", "ollama")


def test_detects_an_openai_server_without_v1_and_stores_the_address_up_to_v1(monkeypatch):
    # llama-server: no /api/tags (404), the model list is under /v1/models
    _serve(monkeypatch, {"http://127.0.0.1:8081/api/tags": 404, "http://127.0.0.1:8081/v1/models": OPENAI_MODELS})
    server = detect_server("http://127.0.0.1:8081")
    assert (server.url, server.protocol) == ("http://127.0.0.1:8081/v1", "openai")


def test_detects_an_openai_server_given_with_v1(monkeypatch):
    seen = _serve(monkeypatch, {"http://127.0.0.1:1234/v1/models": OPENAI_MODELS})
    server = detect_server("http://127.0.0.1:1234/v1/")
    assert (server.url, server.protocol) == ("http://127.0.0.1:1234/v1", "openai")
    assert seen == ["http://127.0.0.1:1234/v1/models"]  # asked first, nothing else needed


def test_detects_an_openai_server_that_lists_models_without_the_prefix(monkeypatch):
    _serve(monkeypatch, {"http://h:9/models": OPENAI_MODELS})
    assert detect_server("h:9").url == "http://h:9"  # a bare host:port gets http://


def test_detection_passes_the_api_key(monkeypatch):
    keys = []

    def fake(request, timeout=None):
        keys.append(request.get_header("Authorization"))
        return _Resp(json.dumps(OPENAI_MODELS).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    server = detect_server("http://x:1/v1", api_key="sk-1")
    assert keys == ["Bearer sk-1"] and server.api_key == "sk-1"


@pytest.mark.parametrize(
    ("routes", "reason"),
    [
        ({}, "connection refused"),
        ({"http://x:1/api/tags": 404, "http://x:1/v1/models": 404, "http://x:1/models": 404}, "not an Ollama"),
        ({"http://x:1/api/tags": 401, "http://x:1/v1/models": 401, "http://x:1/models": 401}, "check the API key"),
        (
            {
                "http://x:1/api/tags": urllib.error.URLError(TimeoutError("t")),
                "http://x:1/v1/models": urllib.error.URLError(TimeoutError("t")),
                "http://x:1/models": urllib.error.URLError(TimeoutError("t")),
            },
            "timed out",
        ),
    ],
)
def test_detection_says_why_nothing_was_found(monkeypatch, routes, reason):
    _serve(monkeypatch, routes)
    with pytest.raises(LlmError, match=reason):
        detect_server("http://x:1")


@pytest.mark.parametrize("url", ["", "ftp://x:1", "http://"])
def test_detection_rejects_an_address_that_is_none(monkeypatch, url):
    _serve(monkeypatch, {})
    with pytest.raises(LlmError, match="enter an address"):
        detect_server(url)


# ── model lists ───────────────────────────────────────────────────────────────────────────────────
def test_ollama_server_lists_its_installed_models(monkeypatch):
    _serve(monkeypatch, {"http://o:1/api/tags": TAGS})
    assert server_models(LlmServer("http://o:1", "ollama")) == {
        "reachable": True,
        "models": [{"name": "gemma4:e4b", "size_gb": 6.6}],
    }


def test_openai_server_lists_its_models(monkeypatch):
    _serve(monkeypatch, {"http://o:1/v1/models": OPENAI_MODELS})
    listing = server_models(LlmServer("http://o:1/v1", "openai"))
    assert [m["name"] for m in listing["models"]] == ["qwen-27b", "other"]


def test_unreachable_server_gives_a_short_reason_and_no_models(monkeypatch):
    _serve(monkeypatch, {})
    assert server_models(LlmServer("http://o:1", "ollama")) == {
        "reachable": False,
        "models": [],
        "error": "connection refused",
    }


def test_garbage_answer_is_not_a_model_server(monkeypatch):
    _serve(monkeypatch, {"http://o:1/v1/models": ["not", "a", "dict"]})
    assert server_models(LlmServer("http://o:1/v1", "openai"))["error"] == "not a model server"


def test_the_main_dropdown_lists_the_models_of_an_openai_server(monkeypatch):
    _serve(monkeypatch, {"http://o:1/v1/models": OPENAI_MODELS})
    data = llm.build_model_list(LlmConfig(url="http://o:1/v1", protocol="openai", model="qwen-27b"), True, 16)
    assert data["reachable"] is True and data["recommended"] == []
    assert [m["name"] for m in data["other"]] == ["qwen-27b", "other"]


def test_a_saved_name_an_openai_server_does_not_list_stays_selectable(monkeypatch):
    _serve(monkeypatch, {"http://o:1/v1/models": OPENAI_MODELS})
    data = llm.build_model_list(LlmConfig(url="http://o:1/v1", protocol="openai", model="mine"), True, 16)
    assert data["other"][0] == {"name": "mine", "size_gb": 0.0, "installed": None}


# ── config round trip ─────────────────────────────────────────────────────────────────────────────
def test_saved_servers_survive_a_save_and_load(tmp_path):
    servers = [
        LlmServer("http://127.0.0.1:8081/v1", "openai", "llama.cpp", 'k"ey'),
        LlmServer("http://10.0.0.5:11434", "ollama", "Büro ä", ""),
    ]
    path = tmp_path / "config.toml"
    save_config(Config(llm=LlmConfig(servers=servers)), path)
    assert load_config(path).llm.servers == servers


def test_an_old_config_without_servers_loads_unchanged(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[llm]\nurl = "http://127.0.0.1:1234/v1"\nprotocol = "openai"\nmodel = "m"\n', encoding="utf-8")
    loaded = load_config(path).llm
    assert (loaded.url, loaded.protocol, loaded.model, loaded.servers) == (
        "http://127.0.0.1:1234/v1",
        "openai",
        "m",
        [],
    )


def test_unusable_server_entries_are_dropped_on_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[[llm.servers]]\nurl = "http://a:1"\nprotocol = "ollama"\n'
        '[[llm.servers]]\nurl = ""\nprotocol = "ollama"\n'
        '[[llm.servers]]\nurl = "http://b:1"\nprotocol = "grpc"\n'
        '[[llm.servers]]\nurl = "http://a:1/"\nprotocol = "openai"\n',
        encoding="utf-8",
    )
    assert [s.url for s in load_config(path).llm.servers] == ["http://a:1"]


# ── the request: no thinking, thinking stripped, a hard deadline ──────────────────────────────────
class _Capture:
    def __init__(self, *answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, request, timeout=None):
        self.bodies.append(json.loads(request.data))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Resp(json.dumps(answer).encode())


def _chat(text):
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture(autouse=True)
def _forget_rejections():
    llm._NO_THINKING_REJECTED.clear()
    yield
    llm._NO_THINKING_REJECTED.clear()


def test_openai_request_asks_the_model_not_to_think(monkeypatch):
    server = _Capture(_chat("ok"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    complete(LlmConfig(url="http://o:1/v1", protocol="openai", model="m"), "s", "u")
    assert server.bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_a_server_that_refuses_the_field_is_asked_again_without_it_and_remembered(monkeypatch):
    refuse = _http_error("http://o:1/v1/chat/completions", 400)
    server = _Capture(refuse, _chat("fine"), _chat("again"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    config = LlmConfig(url="http://o:1/v1", protocol="openai", model="m")
    assert complete(config, "s", "u") == "fine"
    assert "chat_template_kwargs" in server.bodies[0] and "chat_template_kwargs" not in server.bodies[1]
    assert complete(config, "s", "u") == "again"
    assert "chat_template_kwargs" not in server.bodies[2]  # not tried again


def test_other_client_errors_are_not_retried(monkeypatch):
    server = _Capture(_http_error("http://o:1/v1/chat/completions", 401))
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    with pytest.raises(LlmError):
        complete(LlmConfig(url="http://o:1/v1", protocol="openai"), "s", "u")
    assert len(server.bodies) == 1


def test_ollama_request_has_no_template_field(monkeypatch):
    server = _Capture({"message": {"content": "ok"}})
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    complete(LlmConfig(), "s", "u")
    assert "chat_template_kwargs" not in server.bodies[0] and server.bodies[0]["think"] is False


@pytest.mark.parametrize(
    "reply",
    [
        "<think>hmm, the user wants\nshort</think>\n\nShort.",
        "hmm, the template opened the block\n</think>Short.",
        "Short.",
    ],
)
def test_thinking_that_arrives_anyway_is_stripped(monkeypatch, reply):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture(_chat(reply)))
    assert complete(LlmConfig(url="http://o:1/v1", protocol="openai"), "s", "u") == "Short."


def test_a_reply_that_is_only_thinking_is_no_text(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture(_chat("<think>still going</think>")))
    with pytest.raises(LlmError, match="no text"):
        complete(LlmConfig(url="http://o:1/v1", protocol="openai"), "s", "u")


def test_a_request_that_never_answers_ends_at_the_deadline(monkeypatch):
    release = threading.Event()

    def stuck(request, timeout=None):
        release.wait(10)  # a server that accepted the request and then trickles or says nothing
        raise TimeoutError("late")

    monkeypatch.setattr(llm.urllib.request, "urlopen", stuck)
    started = time.monotonic()
    try:
        with pytest.raises(LlmError) as caught:
            complete(LlmConfig(url="http://o:1/v1", protocol="openai", timeout_s=0.3), "s", "u")
    finally:
        release.set()
    assert caught.value.timed_out is True
    assert time.monotonic() - started < 3


# ── the dashboard ─────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def api(db, session_factory, monkeypatch):
    saved = []
    monkeypatch.setattr("nexusvox.dashboard.api_settings.save_config", lambda cfg, path=None: saved.append(cfg))
    monkeypatch.setattr("nexusvox.dashboard.api_settings.cuda_available", lambda: False)
    api = DashboardAPI(session_factory, Config(llm=LlmConfig(model="gemma4:e2b")), db=db)
    api.saved = saved
    return api


def test_adding_a_server_detects_and_saves_it(api, monkeypatch):
    _serve(monkeypatch, {"http://127.0.0.1:8081/v1/models": OPENAI_MODELS})
    resp = api.add_llm_server("http://127.0.0.1:8081", "", "")
    assert resp["server"] == {"name": "127.0.0.1:8081", "url": "http://127.0.0.1:8081/v1", "protocol": "openai"}
    assert api._config.llm.servers == [LlmServer("http://127.0.0.1:8081/v1", "openai", "127.0.0.1:8081", "")]
    assert api.saved  # written to the config file
    assert api.add_llm_server("http://127.0.0.1:8081", "", "")["error"] == "That server is already saved"


def test_adding_a_dead_server_saves_nothing_and_says_why(api, monkeypatch):
    _serve(monkeypatch, {})
    resp = api.add_llm_server("http://127.0.0.1:9", "", "x")
    assert "connection refused" in resp["error"] and api._config.llm.servers == []
    assert not api.saved


def test_the_dropdown_data_groups_models_by_server_and_marks_a_dead_one(api, monkeypatch):
    api._config.llm.servers = [
        LlmServer("http://live:1/v1", "openai", "Live"),
        LlmServer("http://dead:1", "ollama", "Dead"),
    ]
    _serve(
        monkeypatch,
        {"http://127.0.0.1:11434/api/tags": TAGS, "http://live:1/v1/models": OPENAI_MODELS},
    )
    servers = {s["name"]: s for s in api.get_llm_models()["servers"]}
    assert [m["name"] for m in servers["Live"]["models"]] == ["qwen-27b", "other"]
    assert servers["Dead"]["reachable"] is False and servers["Dead"]["error"] == "connection refused"
    assert not servers["Live"]["active"] and not servers["Dead"]["active"]


def test_picking_a_model_of_a_saved_server_sets_url_protocol_key_and_model(api, monkeypatch):
    _serve(monkeypatch, {})
    api._config.llm.servers = [LlmServer("http://live:1/v1", "openai", "Live", "sk-9")]
    api.select_llm_server_model("http://live:1/v1", "qwen-27b")
    llm_config = api._config.llm
    assert (llm_config.url, llm_config.protocol, llm_config.api_key, llm_config.model) == (
        "http://live:1/v1",
        "openai",
        "sk-9",
        "qwen-27b",
    )
    # The Ollama server used until now stays in the dropdown.
    assert LlmServer("http://127.0.0.1:11434", "ollama", "127.0.0.1:11434", "") in llm_config.servers
    assert api.select_llm_server_model("http://nope:1", "m")["error"] == "That server is not saved"


def test_removing_a_server_keeps_the_one_in_use(api):
    api._config.llm.servers = [LlmServer("http://a:1", "ollama", "A"), LlmServer("http://b:1", "ollama", "B")]
    assert api.remove_llm_server("http://a:1/") == {"ok": True}
    assert [s.name for s in api._config.llm.servers] == ["B"]
    assert api.remove_llm_server("http://a:1")["error"] == "That server is not saved"
    assert api._config.llm.url == "http://127.0.0.1:11434"


def test_server_routes(flask_client, monkeypatch, patch_api):
    patch_api("cuda_available", lambda: False)
    _serve(monkeypatch, {"http://127.0.0.1:11434/api/tags": TAGS, "http://h:1/v1/models": OPENAI_MODELS})
    added = flask_client.post("/api/settings/llm-servers", json={"url": "http://h:1/v1", "name": "H"})
    assert added.status_code == 200 and added.get_json()["server"]["protocol"] == "openai"
    assert flask_client.post("/api/settings/llm-servers", json={"url": "http://dead:1"}).status_code == 400
    models = flask_client.get("/api/settings/llm-models").get_json()
    assert [s["name"] for s in models["servers"]] == ["H"]
    picked = flask_client.post("/api/settings/llm-select", json={"url": "http://h:1/v1", "model": "other"})
    assert picked.status_code == 200 and picked.get_json()["llm_model"] == "other"
    assert flask_client.post("/api/settings/llm-select", json={"url": "http://x:1", "model": "m"}).status_code == 400
    assert flask_client.delete("/api/settings/llm-servers", json={"url": "http://h:1/v1"}).status_code == 200


# ── the page ──────────────────────────────────────────────────────────────────────────────────────
START = "// ── LLM card refresh"
END = "// ── end of LLM card refresh"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_dropdown_groups_per_server_and_a_dead_one_cannot_be_picked():
    source = dashboard_source()
    helpers = source[source.index(START) : source.index(END)]
    script = (
        helpers
        + """
        const data = {reachable: true, protocol: "ollama", selected: "gemma4:e4b",
          recommended: [{name: "gemma4:e4b", size_gb: 6.6, installed: true, recommended_here: true}],
          other: [], servers: [
            {name: "Me", url: "http://me:1", protocol: "ollama", active: true, reachable: true, models: []},
            {name: "Llama", url: "http://l:1/v1", protocol: "openai", active: false, reachable: true,
             models: [{name: "qwen-27b"}]},
            {name: "Gone", url: "http://g:1", protocol: "ollama", active: false, reachable: false,
             error: "connection refused", models: []}]};
        const groups = llmOptionGroups(data);
        const picked = llmServerChoice(groups[1].options[0].value);
        console.log(JSON.stringify([groups, picked, llmServerChoice("gemma4:e4b")]));
        """
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    ).stdout
    groups, choice, plain = json.loads(out)
    assert [g["label"] for g in groups] == [
        "Recommended",
        "Llama",
        "Gone — not answering",
    ]  # the active one is not twice
    assert choice == ["http://l:1/v1", "qwen-27b"] and plain is None
    assert groups[2]["down"] is True
    assert groups[2]["options"] == [
        {"value": "", "label": "No answer: connection refused", "disabled": True, "down": True}
    ]
