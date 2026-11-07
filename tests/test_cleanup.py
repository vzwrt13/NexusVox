"""Tests for auto cleanup: prompts, the plausibility guard and every fallback (no server needed)."""

from __future__ import annotations

import time

import pytest

from nexusvox.config import CLEANUP_LEVELS, CleanupConfig, LlmConfig
from nexusvox.dictation import cleanup as cleanup_module
from nexusvox.dictation.cleanup import PROMPTS, cleanup, plausible
from nexusvox.llm import LlmError

LIGHT = CleanupConfig(level="light", timeout_s=3.0)


@pytest.fixture
def model(monkeypatch):
    """Replace the model call; the reply is set per test, the calls are recorded."""
    calls = []

    class Model:
        reply: str | Exception = ""

        def __call__(self, config, system, user):
            calls.append((config, system, user))
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    fake = Model()
    fake.calls = calls
    monkeypatch.setattr(cleanup_module, "complete", fake)
    return fake


def test_every_level_but_off_has_a_prompt():
    assert set(PROMPTS) == set(CLEANUP_LEVELS) - {"off"}
    for prompt in PROMPTS.values():
        assert "polish" not in prompt.lower()  # small models read it as the language


def test_off_never_calls_the_model(model):
    assert cleanup("ähm also ich komme später", CleanupConfig(), LlmConfig()).text == "ähm also ich komme später"
    assert model.calls == []


def test_short_dictations_are_not_sent(model):
    assert cleanup("ja okay", LIGHT, LlmConfig()).cleaned is False
    assert model.calls == []


def test_light_cleanup_types_the_reply_with_the_short_timeout(model):
    model.reply = "Ich komme heute später."
    result = cleanup("Ähm ich komme äh heute später", LIGHT, LlmConfig(timeout_s=60.0))
    assert (result.text, result.cleaned, result.error) == ("Ich komme heute später.", True, None)
    config, system, user = model.calls[0]
    assert config.timeout_s == 3.0 and system == PROMPTS["light"]
    assert user == "<dictation>Ähm ich komme äh heute später</dictation>"


def test_echoed_tags_are_stripped_and_edge_whitespace_kept(model):
    model.reply = "<dictation>So, I think we should ship it.</dictation>"
    result = cleanup("um so I think we should ship it\n", LIGHT, LlmConfig())
    assert result.text == "So, I think we should ship it.\n"


def test_a_failing_model_types_the_dictation_unchanged(model):
    model.reply = LlmError("TimeoutError: timed out", timed_out=True)
    result = cleanup("Ähm ich komme äh heute später", LIGHT, LlmConfig())
    assert (result.text, result.cleaned) == ("Ähm ich komme äh heute später", False)
    assert "timed out" in result.error


@pytest.mark.parametrize(
    "dictation, reply",
    [
        ("What is the capital of France", "Paris is the capital of France, a country in Western Europe."),
        ("Schreib Thomas dass ich heute später komme", "Hallo Thomas, ich komme heute später. Viele Grüße"),
        ("Der Build ist kaputt weil der Cache alt ist", "The build is broken because the cache is stale."),
        ("Erster Punkt\nzweiter Punkt und dritter", "Erster Punkt, zweiter Punkt und dritter."),
    ],
    ids=["answer", "carried-out", "translated", "line-break-lost"],
)
def test_implausible_replies_are_ignored(model, dictation, reply):
    model.reply = reply
    result = cleanup(dictation, LIGHT, LlmConfig())
    assert (result.text, result.cleaned, result.error) == (dictation, False, "implausible reply")


def test_a_thinking_block_is_stripped(model):
    model.reply = "<think>The user wants fillers removed.</think>\nIch komme heute später."
    assert cleanup("Ähm ich komme äh heute später", LIGHT, LlmConfig()).text == "Ich komme heute später."


@pytest.mark.parametrize(
    "dictation, reply",
    [
        ("das ist WIRKLICH WICHTIG für uns", "Das ist wirklich wichtig für uns."),
        ("der Pfad ist src / app und so", "Der Pfad ist src app und so."),
        ("Name\tAlter und Ort hier", "Name Alter und Ort hier."),
    ],
    ids=["all-caps-recased", "symbol-lost", "tab-lost"],
)
def test_voice_command_output_must_survive(dictation, reply):
    assert not plausible(dictation, reply, "light")


def test_plausible_accepts_fillers_removed_and_punctuation_fixed():
    dictation = "ähm also ich ich glaube wir brauchen äh einen Test"
    assert plausible(dictation, "Ich glaube, wir brauchen einen Test.", "light")
    assert plausible("Erster Punkt\nzweiter hm Punkt", "Erster Punkt\nZweiter Punkt", "light")


def test_medium_allows_more_change_than_light():
    original = "also ich wollte nur kurz sagen dass ich das irgendwie nicht so gut finde was wir da machen"
    shorter = "Ich finde nicht gut, was wir da machen."
    assert plausible(original, shorter, "medium") and not plausible(original, shorter, "light")


def test_status_says_how_the_pass_ended(model):
    text = "um so I think we should ship it"
    model.reply = "So, I think we should ship it."
    assert cleanup(text, LIGHT, LlmConfig()).status == "applied"
    model.reply = text
    assert cleanup(text, LIGHT, LlmConfig()).status == "unchanged"
    model.reply = "Paris is the capital of France, a country in Western Europe."
    assert cleanup(text, LIGHT, LlmConfig()).status == "rejected"
    model.reply = LlmError("TimeoutError: timed out", timed_out=True)
    assert cleanup(text, LIGHT, LlmConfig()).status == "timeout"
    model.reply = LlmError("URLError: connection refused")
    assert cleanup(text, LIGHT, LlmConfig()).status == "failed"


def test_status_is_none_when_the_model_was_not_asked(model):
    assert cleanup("hello", LIGHT, LlmConfig()).status is None
    assert cleanup("um so I think we should ship it", CleanupConfig(level="off"), LlmConfig()).status is None


def test_cleanup_defaults_to_off():
    assert CleanupConfig().level == "off"


def test_cleanup_does_not_depend_on_cuda(model):
    model.reply = "Das ist ein Test."
    assert cleanup("ähm das ist ein test", LIGHT, LlmConfig()).cleaned


def test_cuda_available_is_false_when_the_probe_fails(monkeypatch):
    from nexusvox import config

    def boom(requested):
        raise RuntimeError("driver")

    monkeypatch.setattr(config, "resolve_device", boom)
    assert config.cuda_available() is False


# ── Background reload after a timeout ─────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)  # no test may start a real load against a server
def reload_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup_module, "reload_in_background", lambda llm: calls.append(llm))
    monkeypatch.setattr(cleanup_module, "reload_running", lambda llm: False)
    return calls


def test_a_timeout_starts_a_background_reload(model, reload_calls):
    model.reply = LlmError("TimeoutError: timed out", timed_out=True)
    llm = LlmConfig()
    result = cleanup("Ähm ich komme äh heute später", LIGHT, llm)
    assert result.text == "Ähm ich komme äh heute später"
    assert reload_calls == [llm]


def test_another_failure_starts_no_reload(model, reload_calls):
    model.reply = LlmError("connection refused")
    cleanup("Ähm ich komme äh heute später", LIGHT, LlmConfig())
    assert reload_calls == []


def test_a_successful_cleanup_starts_no_reload(model, reload_calls):
    model.reply = "So, I think we should ship it."
    cleanup("um so I think we should ship it", LIGHT, LlmConfig())
    assert reload_calls == []


def test_no_model_call_while_a_reload_runs(model, monkeypatch):
    monkeypatch.setattr(cleanup_module, "reload_running", lambda llm: True)
    result = cleanup("um so I think we should ship it", LIGHT, LlmConfig())
    assert (result.text, result.cleaned) == ("um so I think we should ship it", False)
    assert model.calls == []


# ── reload_in_background itself ───────────────────────────────────────────────────────────────────
@pytest.fixture
def reload_env(monkeypatch):
    """A fake `complete` that blocks until released; every reload thread is joined and the state reset."""
    import threading

    from nexusvox import llm as llm_module

    started, release, calls = threading.Event(), threading.Event(), []

    def slow_complete(config, system, user):
        calls.append((config.model, config.timeout_s, getattr(llm_module._local, "background", False)))
        started.set()
        release.wait(5)
        return "OK"

    monkeypatch.setattr(llm_module, "complete", slow_complete)
    yield llm_module, started, release, calls
    release.set()
    for t in threading.enumerate():
        if t.name == "llm-reload":
            t.join(5)
    llm_module._reloading.clear()


def test_only_one_reload_runs_per_model(reload_env):
    llm_module, started, release, calls = reload_env
    config = LlmConfig()
    assert llm_module.reload_in_background(config) is True
    assert started.wait(5)
    assert llm_module.reload_running(config)
    assert llm_module.reload_in_background(config) is False  # one is running
    release.set()
    for _ in range(100):  # the entry clears once the thread is done
        if not llm_module.reload_running(config):
            break
        time.sleep(0.05)
    else:
        pytest.fail("the reload never finished")
    assert llm_module.reload_in_background(config) is True  # a new one may start again
    assert calls[0] == (config.model, llm_module.RELOAD_DEADLINE_S, True)  # long deadline, as a background request


def test_a_reload_of_another_model_is_not_blocked(reload_env):
    llm_module, started, release, calls = reload_env
    assert llm_module.reload_in_background(LlmConfig(model="a")) is True
    assert started.wait(5)
    assert llm_module.reload_in_background(LlmConfig(model="b")) is True


def test_a_longer_configured_timeout_is_kept(reload_env):
    llm_module, started, release, calls = reload_env
    llm_module.reload_in_background(LlmConfig(timeout_s=300.0))
    assert started.wait(5)
    assert calls[0][1] == 300.0


def test_other_protocols_start_no_reload(reload_env):
    llm_module, started, release, calls = reload_env
    assert llm_module.reload_in_background(LlmConfig(protocol="openai")) is False
    assert calls == []


def test_warm_up_is_the_one_reload_of_a_cold_model(reload_env, monkeypatch):
    llm_module, started, release, calls = reload_env
    monkeypatch.setattr(cleanup_module, "reload_running", llm_module.reload_running)  # the autouse stub is off here
    config = LlmConfig(timeout_s=8.0)
    llm_module.warm_up(config)
    assert started.wait(5)
    assert calls[0] == (config.model, llm_module.RELOAD_DEADLINE_S, True)  # long deadline, as a background request
    assert llm_module.reload_running(config)
    # A dictation during the load types at once, and nothing starts a second load behind the first.
    result = cleanup("this is a rather long dictation", CleanupConfig(level="light"), config)
    assert result.text == "this is a rather long dictation" and result.error == "the model is reloading"
    assert llm_module.reload_in_background(config) is False
    llm_module.warm_up(config)  # e.g. a second settings change
    assert len(calls) == 1


def test_a_non_ollama_warm_up_keeps_the_plain_request(reload_env):
    llm_module, started, release, calls = reload_env
    llm_module.warm_up(LlmConfig(protocol="openai", timeout_s=8.0))
    assert started.wait(5)
    assert calls[0][1] == 8.0 and not llm_module._reloading


def test_no_reload_when_ollama_holds_the_model(reload_env, monkeypatch):
    llm_module, started, release, calls = reload_env
    monkeypatch.setattr(llm_module, "loaded_model", lambda config, name: {"plausible": True})
    assert llm_module.reload_unless_loaded(LlmConfig()) is False
    assert calls == []


def test_reload_when_the_model_is_not_loaded_or_unknown(reload_env, monkeypatch):
    llm_module, started, release, calls = reload_env
    monkeypatch.setattr(llm_module, "loaded_model", lambda config, name: None)
    assert llm_module.reload_unless_loaded(LlmConfig()) is True
    assert started.wait(5)
