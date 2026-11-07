"""Tests for the language model client and polish prompts (no server needed)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from nexusvox import llm
from nexusvox.config import LlmConfig
from nexusvox.dictation import polish as polish_module
from nexusvox.dictation.polish import (
    DEFAULT_INSTRUCTION,
    SYSTEM_PROMPT,
    build_user_message,
    clean_reply,
    polish,
    read_selection,
)
from nexusvox.llm import LlmError, complete


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


@pytest.mark.parametrize(
    ("error", "timed_out"),
    [
        (TimeoutError("slow"), True),
        (urllib.error.URLError(TimeoutError("slow")), True),
        (urllib.error.URLError("refused"), False),
    ],
)
def test_llm_error_tells_a_timeout_from_other_failures(monkeypatch, error, timed_out):
    monkeypatch.setattr(llm.urllib.request, "urlopen", FakeServer(error=error))
    with pytest.raises(LlmError) as caught:
        complete(LlmConfig(), "system", "user")
    assert caught.value.timed_out is timed_out


def test_user_message_uses_the_default_instruction_when_nothing_was_said():
    assert build_user_message("Hallo Welt", "  ").startswith(f"Instruction: {DEFAULT_INSTRUCTION}\n")
    assert build_user_message("Hallo Welt", "mach es formeller").startswith("Instruction: mach es formeller")


ENGLISH = "Hi team, I finished the report yesterday and it still needs a review before we send it to the client."
DUTCH = "Hallo team, ik heb het rapport gisteren afgemaakt en het moet nog worden nagekeken voordat we het versturen."
FRENCH = (
    "Bonjour à tous, j'ai terminé le rapport hier et il doit encore être relu avant que nous l'envoyions au client."
)
GERMAN = "Hallo Team, ich habe den Bericht gestern fertig gemacht und er muss noch geprüft werden."


def test_german_instruction_on_english_text_pins_english():
    message = build_user_message(ENGLISH, "Mach es formeller")
    assert "Language of the text: English." in message and "must stay in English" in message
    assert message.endswith(f"Text:\n{ENGLISH}")


def test_english_instruction_on_german_text_pins_german():
    assert "Language of the text: German." in build_user_message(GERMAN, "Make it more formal")


def test_other_languages_are_named_not_mistaken_for_german():
    assert "Language of the text: Dutch." in build_user_message(DUTCH, "Make it shorter")
    assert "Language of the text: French." in build_user_message(FRENCH, "Mach es kürzer")


def test_the_pin_leaves_room_for_an_explicit_request():
    message = build_user_message(ENGLISH, "Translate this into German")
    assert message.startswith("Instruction: Translate this into German\n")
    assert "asks to translate or to switch language, follow it" in message
    assert "explicit request" in SYSTEM_PROMPT


def test_unsure_detection_adds_no_line(monkeypatch):
    monkeypatch.setattr(polish_module, "detect_selection_language", lambda _selection: None)
    assert build_user_message("ok", "Mach es formeller") == "Instruction: Mach es formeller\n\nText:\nok"


def test_a_mixed_selection_is_not_pinned(monkeypatch):
    class Value:
        def __init__(self, name, value):
            self.language, self.value = type("L", (), {"name": name})(), value

    class Detector:
        def compute_language_confidence_values(self, _text):
            return [Value("GERMAN", 0.55), Value("ENGLISH", 0.45)]

    monkeypatch.setattr(polish_module, "_detector", lambda: Detector())
    assert polish_module.detect_selection_language("Danke!\n> Thanks a lot for the update.") is None


def test_detection_looks_at_the_start_of_a_huge_selection(monkeypatch):
    seen = []

    class Detector:
        def compute_language_confidence_values(self, text):
            seen.append(len(text))
            return []

    monkeypatch.setattr(polish_module, "_detector", lambda: Detector())
    polish_module.detect_selection_language("x" * 100_000)
    assert seen == [polish_module._DETECT_CHARS]


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

    import nexusvox.dictation.polish as polish_module
    from nexusvox.system.uia import Focus

    copies = []
    fake_injector = types.ModuleType("nexusvox.dictation.injector")
    fake_injector.copy_selection = lambda delay: copies.append(delay) or clipboard
    monkeypatch.setitem(sys.modules, "nexusvox.dictation.injector", fake_injector)
    monkeypatch.setattr(polish_module, "ensure_com", lambda: None)
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


def test_warm_up_uses_the_long_reload_deadline(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "complete", lambda config, system, user: seen.append(config.timeout_s) or "OK")
    started = []
    monkeypatch.setattr(llm.threading, "Thread", lambda target, **kw: started.append(target) or _Started(target))
    llm.warm_up(LlmConfig(timeout_s=8.0))
    llm.warm_up(LlmConfig(timeout_s=300.0))
    for run in started:
        run()
    assert seen == [llm.RELOAD_DEADLINE_S, 300.0]  # never shorter than the configured timeout


class _Started:
    def __init__(self, target):
        self.target = target

    def start(self):
        pass


def test_background_requests_wait_for_a_live_request(monkeypatch):
    import threading
    import time

    release_live = threading.Event()
    live_started = threading.Event()
    order: list[str] = []

    def urlopen(request, timeout=None):
        who = json.loads(request.data)["messages"][1]["content"]
        order.append(f"start {who}")
        if who == "live":
            live_started.set()
            release_live.wait(5)
        order.append(f"end {who}")
        return io.BytesIO(json.dumps({"message": {"content": "ok"}}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)

    def background_request():
        with llm.background():
            complete(LlmConfig(), "system", "background")

    live = threading.Thread(target=lambda: complete(LlmConfig(), "system", "live"))
    live.start()
    assert live_started.wait(5)
    bg = threading.Thread(target=background_request)
    bg.start()
    time.sleep(0.2)
    assert order == ["start live"]  # the background request has not been sent
    release_live.set()
    live.join(5)
    bg.join(5)

    assert order == ["start live", "end live", "start background", "end background"]


def test_background_request_goes_ahead_after_the_wait_limit(monkeypatch):
    import threading

    release_live = threading.Event()
    live_started = threading.Event()
    sent: list[str] = []

    def urlopen(request, timeout=None):
        who = json.loads(request.data)["messages"][1]["content"]
        sent.append(who)
        if who == "live":
            live_started.set()
            release_live.wait(5)
        return io.BytesIO(json.dumps({"message": {"content": "ok"}}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(llm, "BACKGROUND_WAIT_S", 0.1)
    live = threading.Thread(target=lambda: complete(LlmConfig(), "system", "live"))
    live.start()
    assert live_started.wait(5)

    with llm.background():
        complete(LlmConfig(), "system", "background")

    release_live.set()
    live.join(5)
    assert sent == ["live", "background"]
