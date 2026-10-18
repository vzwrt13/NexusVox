"""Tests for the language model client and polish prompts (no server needed)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from nexusvox import llm
from nexusvox.config import LlmConfig
from nexusvox.llm import LlmError, complete
from nexusvox.polish import DEFAULT_INSTRUCTION, build_user_message, clean_reply, polish, read_selection


class FakeServer:
    """Stands in for urllib.request.urlopen and records the request."""

    def __init__(self, reply=None, error: Exception | None = None):
        self.reply, self.error, self.requests = reply, error, []

    def __call__(self, request, timeout=None):
        self.requests.append((request.full_url, json.loads(request.data), dict(request.headers), timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.reply).encode())


def test_ollama_protocol_turns_thinking_off_and_keeps_the_model_loaded(monkeypatch):
    server = FakeServer({"message": {"content": " Kürzer. "}})
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)

    assert complete(LlmConfig(keep_alive="2m"), "system", "user") == "Kürzer."
    url, body, _headers, timeout = server.requests[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert (body["think"], body["stream"], body["keep_alive"], body["model"]) == (False, False, "2m", "gemma4:e4b")
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert timeout == 60.0


def test_openai_protocol_and_api_key(monkeypatch):
    server = FakeServer({"choices": [{"message": {"content": "Shorter."}}]})
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    config = LlmConfig(url="http://127.0.0.1:1234/v1/", protocol="openai", model="m", api_key="sk-1")

    assert complete(config, "system", "user") == "Shorter."
    url, body, headers, _ = server.requests[0]
    assert url == "http://127.0.0.1:1234/v1/chat/completions"
    assert "think" not in body and headers["Authorization"] == "Bearer sk-1"


@pytest.mark.parametrize(
    "server",
    [
        FakeServer(error=urllib.error.URLError("refused")),
        FakeServer(error=TimeoutError("slow")),
        FakeServer({"unexpected": True}),
        FakeServer({"message": {"content": "   "}}),
    ],
)
def test_every_failure_is_an_llm_error(monkeypatch, server):
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)
    with pytest.raises(LlmError):
        complete(LlmConfig(), "system", "user")


def test_user_message_uses_the_default_instruction_when_nothing_was_said():
    assert build_user_message("Hallo Welt", "  ") == f"Instruction: {DEFAULT_INSTRUCTION}\n\nText:\nHallo Welt"
    assert build_user_message("Hallo Welt", "mach es formeller").startswith("Instruction: mach es formeller")


def test_clean_reply_strips_a_code_fence():
    assert clean_reply("```text\nGuten Tag.\n```") == "Guten Tag."
    assert clean_reply("  Guten Tag. ") == "Guten Tag."


def test_polish_sends_selection_and_instruction(monkeypatch):
    server = FakeServer({"message": {"content": "```\nSehr geehrte Damen und Herren\n```"}})
    monkeypatch.setattr(llm.urllib.request, "urlopen", server)

    assert polish("hi leute", "mach es formeller", LlmConfig()) == "Sehr geehrte Damen und Herren"
    user = server.requests[0][1]["messages"][1]["content"]
    assert "mach es formeller" in user and user.endswith("hi leute")


# --- reading the selection ---


def _focus(monkeypatch, focus, clipboard="from Ctrl+C"):
    import sys
    import types

    import nexusvox.polish as polish_module
    from nexusvox.uia import Focus

    copies = []
    fake_injector = types.ModuleType("nexusvox.injector")
    fake_injector.copy_selection = lambda delay: copies.append(delay) or clipboard
    monkeypatch.setitem(sys.modules, "nexusvox.injector", fake_injector)
    monkeypatch.setattr(polish_module, "_ensure_com", lambda: None)
    monkeypatch.setattr(polish_module, "read_focus", lambda: Focus(**focus))
    return copies


def test_read_selection_asks_the_control_first(monkeypatch):
    copies = _focus(monkeypatch, {"terminal": False, "selection": "markierter Text"})
    assert read_selection(0) == "markierter Text" and copies == []


def test_read_selection_trusts_an_empty_reported_selection(monkeypatch):
    copies = _focus(monkeypatch, {"terminal": False, "selection": ""})
    assert read_selection(0) == "" and copies == []


def test_read_selection_never_sends_ctrl_c_to_a_terminal(monkeypatch):
    copies = _focus(monkeypatch, {"terminal": True, "selection": None})
    assert read_selection(0) == "" and copies == []


def test_read_selection_copies_from_a_terminal_when_allowed(monkeypatch):
    copies = _focus(monkeypatch, {"terminal": True, "selection": None})
    assert read_selection(3, in_terminals=True) == "from Ctrl+C" and copies == [3]


def test_read_selection_falls_back_to_the_clipboard(monkeypatch):
    copies = _focus(monkeypatch, {"terminal": False, "selection": None})
    assert read_selection(7) == "from Ctrl+C" and copies == [7]


def test_warm_up_loads_the_model_in_the_background_and_never_raises(monkeypatch):
    calls = []

    def fake_complete(config, system, user):
        calls.append(config.model)
        raise LlmError("URLError: refused")

    monkeypatch.setattr(llm, "complete", fake_complete)
    started = []
    monkeypatch.setattr(llm.threading, "Thread", lambda target, **kw: started.append(target) or _Started(target))
    llm.warm_up(LlmConfig(model="gemma4:e4b"))
    started[0]()  # run the thread body here; it must swallow the error
    assert calls == ["gemma4:e4b"]


class _Started:
    def __init__(self, target):
        self.target = target

    def start(self):
        pass
