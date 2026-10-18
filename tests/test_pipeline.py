"""Tests for the text pipeline from raw transcript to typed and saved text."""

from __future__ import annotations

from nexusvox.cleanup import CleanupResult
from nexusvox.config import CleanupConfig, Config, VoiceCommandsConfig
from nexusvox.dictionary import DictionaryEntry
from nexusvox.pipeline import process_text
from nexusvox.snippets import Snippet
from nexusvox.translator import TranslateResult

SIGNATURE = Snippet("meine Signatur", "Viele Grüße\nVincent")


def _config(**voice) -> Config:
    return Config(voice_commands=VoiceCommandsConfig(**voice))


def no_translation(text, _config):
    return TranslateResult(text)


def no_cleanup(text, _config, _llm):
    return CleanupResult(text)


def _run(
    raw, config=None, dictionary=(), snippets=(), remote=False, translate_fn=no_translation, cleanup_fn=no_cleanup
):
    return process_text(
        raw,
        config or _config(),
        list(dictionary),
        list(snippets),
        remote=remote,
        translate_fn=translate_fn,
        cleanup_fn=cleanup_fn,
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


def test_voice_commands_stay_learnable_against_the_spoken_words():
    result = _run("first new line second")
    assert (result.typed, result.learnable, result.spoken) == ("first\nsecond", True, "first new line second")


def test_numbers_as_digits_stay_learnable_against_the_spoken_words():
    result = _run("ich habe fünfundzwanzig Äpfel", _config(numbers_as_digits=True))
    assert (result.typed, result.learnable, result.spoken) == (
        "ich habe 25 Äpfel",
        True,
        "ich habe fünfundzwanzig Äpfel",
    )


def test_press_enter_is_split_and_saved_as_marker_when_alone():
    config = _config(press_enter=True)
    sent = _run("Bin gleich da, Enter drücken.", config)
    alone = _run("press enter", config)
    assert (sent.typed, sent.press_enter, sent.learnable, sent.spoken) == ("Bin gleich da", True, True, "Bin gleich da")
    assert (alone.typed, alone.saved, alone.press_enter) == ("", "[press enter]", True)


def test_press_enter_stays_text_for_the_phone():
    result = _run("Bin gleich da, Enter drücken.", _config(press_enter=True), remote=True)
    assert (result.typed, result.press_enter) == ("Bin gleich da, Enter drücken.", False)


def test_snippet_survives_voice_commands_and_stays_out_of_the_spoken_words():
    result = _run("Danke new line meine Signatur", snippets=[SIGNATURE])
    assert (result.typed, result.saved, result.learnable) == (
        "Danke\nViele Grüße\nVincent",
        "Danke\nViele Grüße\nVincent",
        True,
    )
    assert result.spoken.startswith("Danke new line ") and "Viele" not in result.spoken


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


def test_cleanup_is_typed_and_translated_but_the_spoken_text_is_saved():
    seen = {}

    def tidy(text, config, _llm):
        seen["cleanup"] = (text, config.level)
        return CleanupResult(text.replace("ähm ", ""), cleaned=True, ms=500)

    def to_english(text, _config):
        seen["translate"] = text
        return TranslateResult(text.replace("Hallo", "Hello"), translated=True)

    config = _config()
    config.cleanup = CleanupConfig(level="light")
    result = _run("ähm Hallo Welt", config=config, cleanup_fn=tidy, translate_fn=to_english)
    assert seen == {"cleanup": ("ähm Hallo Welt", "light"), "translate": "Hallo Welt"}
    assert (result.typed, result.saved, result.learnable) == ("Hello Welt", "ähm Hallo Welt", False)
    assert result.cleanup.cleaned is True


def test_cleaned_dictation_is_not_learnable():
    result = _run("ähm Hallo Welt", cleanup_fn=lambda text, _c, _l: CleanupResult("Hallo Welt", cleaned=True))
    assert (result.typed, result.saved, result.learnable) == ("Hallo Welt", "ähm Hallo Welt", False)


def test_cleanup_sees_snippet_placeholders_and_the_snippet_is_expanded_after():
    def keep(text, _c, _l):
        assert "Viele Grüße" not in text  # the stored text is a placeholder here
        return CleanupResult(text.replace("ähm ", ""), cleaned=True)

    result = _run("ähm Danke meine Signatur", snippets=[SIGNATURE], cleanup_fn=keep)
    assert result.typed == "Danke Viele Grüße\nVincent"


def test_english_passed_through_by_the_translator_stays_learnable():
    def passthrough(text, _config):
        return TranslateResult(text, translated=True, source="en")

    result = _run("Hello world", translate_fn=passthrough)
    assert (result.typed, result.learnable) == ("Hello world", True)
