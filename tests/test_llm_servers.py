"""Saved custom language model servers: detection, model lists, the config round trip, the dropdown data,
the request for no thinking and the hard deadline of a slow request."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
from pathlib import Path

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
    _serve(monkeypatch, {"http://127.0.0.1:1234/v1/models": OPENAI_MODELS})
    server = detect_server("http://127.0.0.1:1234/v1/")
    assert (server.url, server.protocol) == ("http://127.0.0.1:1234/v1", "openai")


def test_ollama_entered_with_its_v1_address_is_still_ollama(monkeypatch):
    # Ollama answers /v1/models as well; it must be saved by its own API (think: false, keep_alive, pull).
    _serve(
        monkeypatch,
        {"http://127.0.0.1:11434/api/tags": TAGS, "http://127.0.0.1:11434/v1/models": OPENAI_MODELS},
    )
    server = detect_server("http://127.0.0.1:11434/v1")
    assert (server.url, server.protocol) == ("http://127.0.0.1:11434", "ollama")


def test_the_candidates_are_probed_at_once_so_a_dead_address_costs_one_timeout(monkeypatch):
    release = threading.Event()

    def slow(request, timeout=None):
        release.wait(0.4)  # every probe hangs for 0.4 s, then is refused
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", slow)
    started = time.monotonic()
    with pytest.raises(LlmError):
        detect_server("http://x:1")  # three candidates
    assert time.monotonic() - started < 1.0  # one after the other would take 1.2 s


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
    assert set(keys) == {"Bearer sk-1"} and server.api_key == "sk-1"


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


def _refusal(code: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode())
    return urllib.error.HTTPError("http://o:1/v1/chat/completions", code, "x", {}, body)  # type: ignore[arg-type]


def test_a_server_that_refuses_the_field_is_asked_again_without_it_and_remembered(monkeypatch):
    refuse = _refusal(400, "Unrecognized request argument supplied: chat_template_kwargs")
    server = _Capture(refuse, _chat("fine"), _chat("again"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    config = LlmConfig(url="http://o:1/v1", protocol="openai", model="m")
    assert complete(config, "s", "u") == "fine"
    assert "chat_template_kwargs" in server.bodies[0] and "chat_template_kwargs" not in server.bodies[1]
    assert complete(config, "s", "u") == "again"
    assert "chat_template_kwargs" not in server.bodies[2]  # not tried again


@pytest.mark.parametrize("code", [400, 422])
def test_a_client_error_that_does_not_name_the_field_is_not_blamed_on_it(monkeypatch, code):
    # A prompt over the context size or a wrong model name also answers 400: no retry, no blacklist.
    server = _Capture(_refusal(code, "the request exceeds the available context size"), _chat("later"))
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    config = LlmConfig(url="http://o:1/v1", protocol="openai", model="m")
    with pytest.raises(LlmError):
        complete(config, "s", "u")
    assert len(server.bodies) == 1 and not llm._NO_THINKING_REJECTED
    assert complete(config, "s", "u") == "later"
    assert "chat_template_kwargs" in server.bodies[1]  # still asked to not think


def test_the_retry_shares_the_deadline_of_the_first_request(monkeypatch):
    def slow_then_refuse(request, timeout=None):
        time.sleep(0.35)
        raise _refusal(422, "extra fields not permitted: chat_template_kwargs")

    monkeypatch.setattr(llm.urllib.request, "urlopen", slow_then_refuse)
    started = time.monotonic()
    with pytest.raises(LlmError) as caught:
        complete(LlmConfig(url="http://o:1/v1", protocol="openai", timeout_s=0.5), "s", "u")
    # The retry only had the rest of the 0.5 s, so the whole call stays near it, not near 2 x 0.5 s + 0.35 s.
    assert time.monotonic() - started < 1.2
    assert caught.value.timed_out is True or "422" in str(caught.value)


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
    # The Ollama used until now is not copied into the custom list: it has its own, fixed entry.
    assert [s.url for s in llm_config.servers] == ["http://live:1/v1"]
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


@pytest.mark.parametrize("protocol", ["openai", "ollama"])
def test_a_closing_tag_in_the_text_is_the_users_own_and_stays(monkeypatch, protocol):
    text = "Close it with </think> like this."
    reply = _chat(text) if protocol == "openai" else {"message": {"content": text}}
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture(reply))
    config = LlmConfig(url="http://o:1/v1", protocol=protocol)
    assert llm.clean_reply(complete(config, "s", "u")) == text


def test_a_server_that_trickles_its_answer_is_dropped_at_the_deadline(monkeypatch):
    closed = threading.Event()

    class Trickle:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.set()  # the connection is closed, so the server stops generating

        def read(self, size=-1):
            time.sleep(0.1)  # a byte every 0.1 s keeps a socket timeout from ever firing
            return b" "

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda request, timeout=None: Trickle())
    started = time.monotonic()
    with pytest.raises(LlmError) as caught:
        complete(LlmConfig(url="http://o:1/v1", protocol="openai", timeout_s=0.5), "s", "u")
    assert caught.value.timed_out is True and time.monotonic() - started < 1.5
    assert closed.wait(1)  # no connection is left open behind the timeout


# ── the Models card does not hammer saved servers ─────────────────────────────────────────────────
def test_saved_server_lists_are_reused_between_polls_and_the_server_in_use_is_asked_once(api, monkeypatch):
    api._config.llm = LlmConfig(url="http://here:1/v1", protocol="openai", model="qwen-27b")
    api._config.llm.servers = [
        LlmServer("http://here:1/v1", "openai", "Here"),
        LlmServer("http://dead:1", "ollama", "Dead"),
    ]
    seen = _serve(monkeypatch, {"http://here:1/v1/models": OPENAI_MODELS, "http://127.0.0.1:11434/api/tags": TAGS})
    api.get_llm_models()
    api.get_llm_models()  # the next poll, 5 s later
    assert seen.count("http://dead:1/api/tags") == 1  # the dead one is not probed on every poll
    assert seen.count("http://127.0.0.1:11434/api/tags") == 1  # nor is the built-in Ollama
    assert seen.count("http://here:1/v1/models") == 2  # once per poll (the list of the server in use)
    here = next(s for s in api.get_llm_models()["servers"] if s["active"])
    assert here["reachable"] is True and [m["name"] for m in here["models"]] == ["qwen-27b", "other"]


def test_changing_the_saved_servers_drops_the_reused_lists(api, monkeypatch):
    api._config.llm.servers = [LlmServer("http://dead:1", "ollama", "Dead")]
    seen = _serve(monkeypatch, {"http://h:1/v1/models": OPENAI_MODELS})
    api.get_llm_models()
    api.add_llm_server("http://h:1/v1", "", "")
    api.get_llm_models()
    assert seen.count("http://dead:1/api/tags") == 2


def test_switching_servers_never_changes_the_custom_list(api, monkeypatch):
    api._config.llm.servers = [LlmServer("http://b:1", "ollama", "B")]
    _serve(monkeypatch, {})
    api.select_llm_server_model("http://b:1", "x")  # away from Ollama ...
    api.select_llm_server_model("http://127.0.0.1:11434/", "gemma4:e4b")  # ... and back through its own entry
    llm_config = api._config.llm
    assert (llm_config.url, llm_config.protocol, llm_config.api_key, llm_config.model) == (
        "http://127.0.0.1:11434",
        "ollama",
        "",
        "gemma4:e4b",
    )
    assert [s.url for s in llm_config.servers] == ["http://b:1"]


# ── the built-in Ollama entry ─────────────────────────────────────────────────────────────────────
def test_ollama_that_answers_is_a_fixed_entry_with_its_models(api, monkeypatch):
    api._config.llm = LlmConfig(url="http://live:1/v1", protocol="openai", model="qwen-27b")
    api._config.llm.servers = [LlmServer("http://live:1/v1", "openai", "Live")]
    _serve(monkeypatch, {"http://127.0.0.1:11434/api/tags": TAGS, "http://live:1/v1/models": OPENAI_MODELS})
    data = api.get_llm_models()
    assert data["ollama"] == {
        "name": "Ollama",
        "url": "http://127.0.0.1:11434",
        "protocol": "ollama",
        "active": False,
        "reachable": True,
        "models": [{"name": "gemma4:e4b", "size_gb": 6.6}],
        "state": "running",
    }
    assert [s["name"] for s in data["servers"]] == ["Live"]  # Ollama is never in the custom list


def test_ollama_that_does_not_answer_is_not_an_error(api, monkeypatch):
    api._config.llm = LlmConfig(url="http://live:1/v1", protocol="openai", model="qwen-27b")
    _serve(monkeypatch, {"http://live:1/v1/models": OPENAI_MODELS})
    ollama = api.get_llm_models()["ollama"]
    assert ollama["reachable"] is False and ollama["models"] == [] and "error" not in ollama  # "not installed yet"


def test_the_active_ollama_is_the_built_in_entry_and_asked_once(api, monkeypatch):
    seen = _serve(monkeypatch, {"http://127.0.0.1:11434/api/tags": TAGS})
    ollama = api.get_llm_models()["ollama"]
    assert ollama["active"] is True and ollama["reachable"] is True
    assert seen.count("http://127.0.0.1:11434/api/tags") == 1
    _serve(monkeypatch, {})
    down = api.get_llm_models()["ollama"]
    assert down["active"] is True and down["reachable"] is False


def test_ollama_cannot_be_added_as_a_custom_server(api, monkeypatch):
    _serve(monkeypatch, {"http://127.0.0.1:11434/api/tags": TAGS})
    resp = api.add_llm_server("http://127.0.0.1:11434", "", "")
    assert "built in" in resp["error"] and api._config.llm.servers == []


def test_ollama_has_no_remove(api):
    api._config.llm.servers = [LlmServer("http://a:1", "ollama", "A")]
    assert api.remove_llm_server("http://127.0.0.1:11434")["error"] == "That server is not saved"


def test_an_auto_saved_ollama_entry_is_dropped_on_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[[llm.servers]]\nname = "127.0.0.1:11434"\nurl = "http://127.0.0.1:11434"\nprotocol = "ollama"\n'
        '[[llm.servers]]\nname = "llama.cpp"\nurl = "http://127.0.0.1:8081/v1"\nprotocol = "openai"\n'
        '[[llm.servers]]\nname = "Büro"\nurl = "http://10.0.0.5:11434/"\nprotocol = "ollama"\n',
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert [s.name for s in loaded.llm.servers] == ["llama.cpp", "Büro"]
    save_config(loaded, path)
    written = path.read_text(encoding="utf-8")
    assert written.count("[[llm.servers]]") == 2 and 'name = "127.0.0.1:11434"' not in written  # cleaned for good


def test_an_openai_entry_at_the_ollama_address_is_a_custom_server(tmp_path):
    # Only Ollama's own protocol at the default address is the built-in entry.
    path = tmp_path / "config.toml"
    path.write_text('[[llm.servers]]\nurl = "http://127.0.0.1:11434"\nprotocol = "openai"\n', encoding="utf-8")
    assert [s.protocol for s in load_config(path).llm.servers] == ["openai"]


# ── one log line per request ──────────────────────────────────────────────────────────────────────
def _request_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "nexusvox.llm" and "LLM request" in r.getMessage()]


def test_every_request_logs_one_line_with_server_model_duration_and_outcome(monkeypatch, caplog):
    caplog.set_level("INFO", logger="nexusvox.llm")
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture(_chat("Short.")))
    config = LlmConfig(url="http://user:pw@127.0.0.1:8081/v1/", protocol="openai", model="qwen", api_key="sk-secret")
    complete(config, "SYSTEM SECRET", "the dictated sentence")
    (line,) = _request_lines(caplog)
    assert line.startswith("LLM request: http://127.0.0.1:8081/v1 (openai), model qwen, ")
    assert line.endswith(" ms, ok")
    for secret in ("sk-secret", "pw", "SYSTEM SECRET", "dictated sentence", "Short."):
        assert secret not in line


def test_the_log_line_says_when_thinking_was_stripped(monkeypatch, caplog):
    caplog.set_level("INFO", logger="nexusvox.llm")
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture(_chat("<think>hm</think>Short.")))
    complete(LlmConfig(url="http://o:1/v1", protocol="openai"), "s", "u")
    assert _request_lines(caplog)[0].endswith("ok, thinking stripped")


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (TimeoutError("slow"), "timeout"),
        (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "error: connection refused"),
        (_http_error("http://o:1/api/chat", 500), "error: HTTP 500"),
    ],
)
def test_the_log_line_names_a_failure_briefly(monkeypatch, caplog, error, outcome):
    caplog.set_level("INFO", logger="nexusvox.llm")
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture(error))
    with pytest.raises(LlmError):
        complete(LlmConfig(), "s", "u")
    assert _request_lines(caplog)[0].endswith(f"ms, {outcome}")


def test_an_empty_reply_is_logged_as_an_error(monkeypatch, caplog):
    caplog.set_level("INFO", logger="nexusvox.llm")
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Capture({"message": {"content": "  "}}))
    with pytest.raises(LlmError):
        complete(LlmConfig(), "s", "u")
    assert _request_lines(caplog)[0].endswith("error: the model returned no text")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("ollama", "expected"),
    [
        (
            {"url": "http://127.0.0.1:11434", "active": False, "reachable": False, "models": []},
            {"label": "Ollama — not installed yet", "down": None},
        ),
        (
            {"url": "http://127.0.0.1:11434", "active": False, "reachable": True, "models": [{"name": "gemma4:e4b"}]},
            {"label": "Ollama", "down": None},
        ),
    ],
)
def test_dropdown_shows_the_built_in_ollama_and_never_in_red(ollama, expected):
    source = dashboard_source()
    helpers = source[source.index(START) : source.index(END)]
    script = (
        helpers
        + f"""
        const data = {{reachable: true, protocol: "openai", selected: "m", recommended: [], other: [],
          servers: [], ollama: {json.dumps(ollama)}}};
        console.log(JSON.stringify(llmOptionGroups(data)));
        """
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    ).stdout
    (group,) = json.loads(out)
    assert group["label"] == expected["label"] and "down" not in group
    assert not any(o.get("down") for o in group["options"])
    if ollama["reachable"]:
        assert group["options"][0]["value"].startswith("@@") and group["options"][0]["label"] == "gemma4:e4b"
    else:
        assert group["options"][0]["disabled"] is True  # nothing to pick yet


# ── Ollama: running, installed but stopped, not installed ─────────────────────────────────────────
def _fake_ollama_install(tmp_path, monkeypatch, *, tray_app=True):
    folder = tmp_path / "Programs" / "Ollama"
    folder.mkdir(parents=True)
    (folder / "ollama.exe").write_bytes(b"")
    if tray_app:
        (folder / "ollama app.exe").write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    return folder


def test_ollama_is_found_in_its_default_install_folder(tmp_path, monkeypatch):
    folder = _fake_ollama_install(tmp_path, monkeypatch)
    assert llm.ollama_executable() == folder / "ollama.exe"


def test_ollama_is_found_on_the_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"D:\tools\ollama.exe")
    assert "tools" in str(llm.ollama_executable())


def test_ollama_that_is_not_installed_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    assert llm.ollama_executable() is None
    with pytest.raises(LlmError, match="not installed"):
        llm.start_ollama()


class _Popen:
    started: list = []

    def __init__(self, command, **kwargs):
        _Popen.started.append((command, kwargs))


def test_starting_ollama_opens_its_tray_app_like_the_start_menu(tmp_path, monkeypatch):
    folder = _fake_ollama_install(tmp_path, monkeypatch)
    _Popen.started = []
    monkeypatch.setattr(llm.subprocess, "Popen", _Popen)
    llm.start_ollama()
    ((command, kwargs),) = _Popen.started
    assert command == [str(folder / "ollama app.exe")]
    assert kwargs["stdin"] is llm.subprocess.DEVNULL and kwargs["close_fds"] is True  # detached, no console


def test_without_the_tray_app_ollama_serve_is_started(tmp_path, monkeypatch):
    folder = _fake_ollama_install(tmp_path, monkeypatch, tray_app=False)
    _Popen.started = []
    monkeypatch.setattr(llm.subprocess, "Popen", _Popen)
    llm.start_ollama()
    assert _Popen.started[0][0] == [str(folder / "ollama.exe"), "serve"]


def test_a_start_that_fails_is_an_llm_error(tmp_path, monkeypatch):
    _fake_ollama_install(tmp_path, monkeypatch)

    def refuse(command, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(llm.subprocess, "Popen", refuse)
    with pytest.raises(LlmError, match="could not start Ollama"):
        llm.start_ollama()


@pytest.mark.parametrize(
    ("answers", "installed", "state"),
    [
        (True, True, "running"),
        (False, True, "stopped"),
        (False, False, "not_installed"),
    ],
)
def test_the_ollama_entry_tells_running_stopped_and_not_installed_apart(api, monkeypatch, answers, installed, state):
    api._config.llm = LlmConfig(url="http://live:1/v1", protocol="openai", model="qwen-27b")
    routes = {"http://live:1/v1/models": OPENAI_MODELS}
    if answers:
        routes["http://127.0.0.1:11434/api/tags"] = TAGS
    _serve(monkeypatch, routes)
    monkeypatch.setattr(
        "nexusvox.dashboard.api_settings.ollama_executable", lambda: Path("ollama.exe") if installed else None
    )
    ollama = api.get_llm_models()["ollama"]
    assert ollama["state"] == state and ollama["reachable"] is answers and "error" not in ollama


class _Answers:
    """urlopen for Ollama that starts answering after *after* calls; records the URLs asked."""

    def __init__(self, after):
        self.after, self.calls, self.urls = after, 0, []

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.urls.append(request.full_url)
        if self.calls <= self.after:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))
        return _Resp(json.dumps(TAGS).encode())


@pytest.fixture
def quick_start(monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api_settings.OLLAMA_START_WAIT_S", 0.4)
    monkeypatch.setattr("nexusvox.dashboard.api_settings.OLLAMA_START_POLL_S", 0.02)
    started = []
    monkeypatch.setattr("nexusvox.dashboard.api_settings.llm_start_ollama", lambda: started.append(True))
    return started


def test_start_ollama_starts_it_waits_for_the_server_and_reports_running(api, monkeypatch, quick_start):
    server = _Answers(after=4)  # the check before the start, then a few polls without an answer
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    assert api.start_ollama() == {"ok": True, "running": True}
    assert quick_start == [True] and server.calls == 5
    assert all(url.endswith("/api/tags") for url in server.urls)  # it only asks; nothing is pulled or downloaded


def test_start_ollama_does_not_start_a_second_one(api, monkeypatch, quick_start):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Answers(after=0))
    assert api.start_ollama() == {"ok": True, "running": True} and quick_start == []


def test_start_ollama_gives_a_hint_when_it_does_not_answer_in_time(api, monkeypatch, quick_start):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Answers(after=10_000))
    resp = api.start_ollama()
    assert resp["running"] is False and "does not answer yet" in resp["hint"] and "error" not in resp


def test_start_ollama_says_when_it_is_not_installed(api, monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Answers(after=10_000))
    monkeypatch.setattr(llm, "ollama_executable", lambda: None)
    assert api.start_ollama() == {"error": "Ollama is not installed"}


def test_start_ollama_route(flask_client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api_settings.llm_start_ollama", lambda: None)
    monkeypatch.setattr("nexusvox.dashboard.api_settings.OLLAMA_START_WAIT_S", 0.1)
    monkeypatch.setattr("nexusvox.dashboard.api_settings.OLLAMA_START_POLL_S", 0.02)
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Answers(after=10_000))
    resp = flask_client.post("/api/settings/ollama-start", json={})
    assert resp.status_code == 200 and resp.get_json()["running"] is False
    monkeypatch.setattr(llm.urllib.request, "urlopen", _Answers(after=0))
    assert flask_client.post("/api/settings/ollama-start", json={}).get_json() == {"ok": True, "running": True}


_FAKE_DOM = """
function el() {
  const e = {hidden: true, children: [], _t: '', _c: new Set(),
    append(c) { this.children.push(c); },
    classList: {add(c) { e._c.add(c); }, remove(...c) { c.forEach((x) => e._c.delete(x)); }}};
  Object.defineProperty(e, 'textContent', {get() { return e._t; }, set(v) { e._t = v; e.children = []; }});
  return e;
}
const document = {createElement: () => ({})};
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_page_offers_start_for_a_stopped_ollama_and_install_for_a_missing_one():
    source = dashboard_source()
    status = re.search(r"const STATUS_STATES.*?\n\}\n", source, re.S).group(0)
    hint = source[source.index("// ── Ollama install hint") : source.index("// ── end of Ollama install hint")]
    helpers = source[source.index(START) : source.index(END)]
    script = (
        _FAKE_DOM
        + status
        + hint
        + helpers
        + """
        let started = null;
        function startOllama(button) { started = button; }
        const s = {reachable: false, protocol: 'ollama', url: 'http://127.0.0.1:11434', model: 'm'};
        const stopped = el(), missing = el();
        renderModelsHint(stopped, s, {reachable: false, protocol: 'ollama', ollama: {state: 'stopped'}});
        renderModelsHint(missing, s, {reachable: false, protocol: 'ollama', ollama: {state: 'not_installed'}});
        const button = stopped.children[0];
        button.onclick();
        const groups = llmOptionGroups({reachable: true, protocol: 'openai', selected: 'm', recommended: [],
          other: [], servers: [], ollama: {url: 'x', active: false, reachable: false, state: 'stopped', models: []}});
        console.log(JSON.stringify({
          stopped: [stopped.textContent, button.textContent, stopped._c.has('down'), stopped._c.has('warn')],
          started: started === button,
          missing: [missing.textContent.slice(0, 13), missing.children[0].textContent],
          group: groups[0].label,
        }));
        """
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True, timeout=30
    ).stdout
    result = json.loads(out)
    assert result["stopped"] == ["Ollama is installed but not running. ", "Start Ollama", False, False]
    assert result["started"] is True
    assert result["missing"] == ["Not installed", "Install Ollama"]
    assert result["group"] == "Ollama — installed, not running"
