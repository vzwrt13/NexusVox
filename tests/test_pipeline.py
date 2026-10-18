"""Tests for the text pipeline from raw transcript to typed and saved text."""

from __future__ import annotations

from nexusvox.config import Config, VoiceCommandsConfig
from nexusvox.dictionary import DictionaryEntry
from nexusvox.pipeline import process_text
from nexusvox.snippets import Snippet
from nexusvox.translator import TranslateResult

SIGNATURE = Snippet("meine Signatur", "Viele Grüße\nVincent")


def _config(**voice) -> Config:
    return Config(voice_commands=VoiceCommandsConfig(**voice))


def no_translation(text, _config):
    return TranslateResult(text)


def _run(raw, config=None, dictionary=(), snippets=(), remote=False, translate_fn=no_translation):
    return process_text(
        raw, config or _config(), list(dictionary), list(snippets), remote=remote, translate_fn=translate_fn
    )


def test_plain_dictation_is_learnable():
    result = _run("Hallo Welt")
    assert (result.typed, result.saved, result.press_enter, result.learnable) == (
        "Hallo Welt",
        "Hallo Welt",
        False,
        True,
    )


def test_dictionary_runs_first_and_stays_learnable():
    result = _run("I use nexus fox", dictionary=[DictionaryEntry("nexus fox", "NexusVox")])
    assert (result.typed, result.learnable) == ("I use NexusVox", True)


def test_voice_commands_make_it_unlearnable():
    result = _run("first new line second")
    assert (result.typed, result.learnable) == ("first\nsecond", False)


def test_press_enter_is_split_and_saved_as_marker_when_alone():
    config = _config(press_enter=True)
    sent = _run("Bin gleich da, Enter drücken.", config)
    alone = _run("press enter", config)
    assert (sent.typed, sent.press_enter, sent.learnable) == ("Bin gleich da", True, False)
    assert (alone.typed, alone.saved, alone.press_enter) == ("", "[press enter]", True)


def test_press_enter_stays_text_for_the_phone():
    result = _run("Bin gleich da, Enter drücken.", _config(press_enter=True), remote=True)
    assert (result.typed, result.press_enter) == ("Bin gleich da, Enter drücken.", False)


def test_snippet_survives_voice_commands_and_is_not_learnable():
    result = _run("Danke new line meine Signatur", snippets=[SIGNATURE])
    assert (result.typed, result.saved, result.learnable) == (
        "Danke\nViele Grüße\nVincent",
        "Danke\nViele Grüße\nVincent",
        False,
    )


def test_translation_is_typed_but_the_spoken_text_is_saved():
    def to_english(text, _config):
        return TranslateResult(text.replace("Hallo", "Hello"), translated=True, source="de")

    result = _run("Hallo meine Signatur", snippets=[SIGNATURE], translate_fn=to_english)
    assert (result.typed, result.saved, result.learnable) == (
        "Hello Viele Grüße\nVincent",
        "Hallo Viele Grüße\nVincent",
        False,
    )
    assert result.translation.translated is True


def test_translator_losing_a_placeholder_falls_back_to_the_original():
    def lossy(_text, _config):
        return TranslateResult("Hello", translated=True)

    result = _run("Hallo meine Signatur", snippets=[SIGNATURE], translate_fn=lossy)
    assert result.typed == "Hallo Viele Grüße\nVincent"
    assert (result.translation.translated, result.translation.error) == (False, "snippet placeholder lost")
