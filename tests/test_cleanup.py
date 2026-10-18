"""Tests for auto cleanup: prompts, the plausibility guard and every fallback (no server needed)."""

from __future__ import annotations

import pytest

from nexusvox import cleanup as cleanup_module
from nexusvox.cleanup import PROMPTS, cleanup, plausible
from nexusvox.config import CLEANUP_LEVELS, CleanupConfig, LlmConfig
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
    model.reply = LlmError("TimeoutError: timed out")
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
        ("Hallo Signatur \ue000 bis bald", "Hallo Signatur, bis bald."),
    ],
    ids=["answer", "carried-out", "translated", "line-break-lost", "placeholder-lost"],
)
def test_implausible_replies_are_ignored(model, dictation, reply):
    model.reply = reply
    result = cleanup(dictation, LIGHT, LlmConfig())
    assert (result.text, result.cleaned, result.error) == (dictation, False, "implausible reply")


def test_plausible_accepts_fillers_removed_and_punctuation_fixed():
    dictation = "ähm also ich ich glaube wir brauchen äh einen Test"
    assert plausible(dictation, "Ich glaube, wir brauchen einen Test.", "light")
    assert plausible("Erster Punkt\nzweiter hm Punkt", "Erster Punkt\nZweiter Punkt", "light")
    assert plausible("Hallo \ue000 bis bald", "Hallo \ue000, bis bald.", "light")


def test_medium_allows_more_change_than_light():
    original = "also ich wollte nur kurz sagen dass ich das irgendwie nicht so gut finde was wir da machen"
    shorter = "Ich finde nicht gut, was wir da machen."
    assert plausible(original, shorter, "medium") and not plausible(original, shorter, "light")
