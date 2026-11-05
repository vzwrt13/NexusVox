"""Tests for the text pipeline from raw transcript to typed and saved text."""

from __future__ import annotations

from nexusvox.cleanup import CleanupResult
from nexusvox.config import CleanupConfig, Config, VoiceCommandsConfig
from nexusvox.dictionary import DictionaryEntry
from nexusvox.pipeline import process_text
from nexusvox.translator import TranslateResult


def _config(**voice) -> Config:
    return Config(voice_commands=VoiceCommandsConfig(**voice))


def no_translation(text, _config):
    return TranslateResult(text)


def no_cleanup(text, _config, _llm):
    return CleanupResult(text)


def _run(raw, config=None, dictionary=(), remote=False, translate_fn=no_translation, cleanup_fn=no_cleanup):
    return process_text(
        raw,
        config or _config(),
        list(dictionary),
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


def test_translation_is_typed_but_the_spoken_text_is_saved():
    def to_english(text, _config):
        return TranslateResult(text.replace("Hallo", "Hello"), translated=True, source="de")

    result = _run("Hallo Welt", translate_fn=to_english)
    assert (result.typed, result.saved, result.learnable) == ("Hello Welt", "Hallo Welt", False)
    assert result.translation.translated is True


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


def test_english_passed_through_by_the_translator_stays_learnable():
    def passthrough(text, _config):
        return TranslateResult(text, translated=True, source="en")

    result = _run("Hello world", translate_fn=passthrough)
    assert (result.typed, result.learnable) == ("Hello world", True)


def test_extend_fn_changes_what_is_typed_but_not_what_is_saved():
    from nexusvox.pipeline import process_text

    result = process_text(
        "hello world",
        _config(),
        [],
        translate_fn=no_translation,
        cleanup_fn=no_cleanup,
        extend_fn=lambda text: text.upper(),
    )
    assert (result.typed, result.saved, result.learnable) == ("HELLO WORLD", "hello world", False)


def test_without_extend_fn_nothing_changes():
    assert _run("hello world").typed == "hello world"


def test_a_failing_extend_fn_types_the_text_unchanged():
    def boom(_text):
        raise RuntimeError("nope")

    result = process_text(
        "hello world", _config(), [], translate_fn=no_translation, cleanup_fn=no_cleanup, extend_fn=boom
    )
    assert (result.typed, result.learnable) == ("hello world", True)


def test_extend_fn_is_not_called_for_an_enter_only_dictation():
    calls = []
    result = process_text(
        "press enter",
        _config(press_enter=True),
        [],
        translate_fn=no_translation,
        cleanup_fn=no_cleanup,
        extend_fn=lambda text: calls.append(text) or text + "!",
    )
    assert (result.typed, result.press_enter, calls) == ("", True, [])


def test_a_skipped_cleanup_logs_why(caplog):
    config = _config()
    config.cleanup = CleanupConfig(level="light")
    with caplog.at_level("INFO", logger="nexusvox.pipeline"):
        _run("hello there", config=config)
    assert "Cleanup (light): skipped" in caplog.text


def test_cleanup_off_is_not_logged_at_info(caplog):
    with caplog.at_level("INFO", logger="nexusvox.pipeline"):
        _run("hello there my friend")
    assert "Cleanup" not in caplog.text


def test_dictionary_hits_are_reported():
    fox = DictionaryEntry("nexus fox", "NexusVox")
    unused = DictionaryEntry("never", "Always")

    result = _run("nexus fox and nexus fox", dictionary=[fox, unused])

    assert result.dictionary_hits == {fox: 2}


def test_no_dictionary_hits_without_entries():
    assert _run("hello there").dictionary_hits == {}


def test_german_dictionary_hits_are_reported():
    entry = DictionaryEntry("grüße", "Grüße")

    result = _run("viele grüße und grüße", dictionary=[entry])

    assert result.dictionary_hits == {entry: 2}


# -- extension protect / restore ---------------------------------------------------------------------------


def _protect(trigger="my address", token="⟦1⟧", expansion="Hauptstrasse 1"):
    """A protect_fn like an extension's: swaps the trigger for a token and restores the expansion."""
    calls = {"protect": [], "restore": []}

    def restore(text):
        calls["restore"].append(text)
        return text.replace(token, expansion) if token in text else None

    def protect(text):
        calls["protect"].append(text)
        return (text.replace(trigger, token), restore) if trigger in text else None

    return protect, calls


def _run_protected(raw, protect_fn, config=None, remote=False, translate_fn=no_translation, cleanup_fn=no_cleanup):
    return process_text(
        raw,
        config or _config(),
        [],
        remote=remote,
        translate_fn=translate_fn,
        cleanup_fn=cleanup_fn,
        protect_fn=protect_fn,
    )


def test_protect_runs_before_voice_commands_cleanup_and_translation_and_restore_after_them():
    protect, calls = _protect()
    seen = {}

    def cleanup_fn(text, _c, _l):
        seen["cleanup"] = text
        return CleanupResult(text.replace("ähm ", ""), cleaned=True)

    def translate_fn(text, _c):
        seen["translate"] = text
        return TranslateResult(text.replace("schick das an", "send that to"), translated=True, source="de")

    result = _run_protected("ähm schick das an my address", protect, cleanup_fn=cleanup_fn, translate_fn=translate_fn)
    assert seen == {"cleanup": "ähm schick das an ⟦1⟧", "translate": "schick das an ⟦1⟧"}
    assert result.typed == "send that to Hauptstrasse 1"
    assert result.saved == "ähm schick das an Hauptstrasse 1"  # as spoken, restored
    assert result.learnable is False
    assert calls["restore"] == ["send that to ⟦1⟧", "ähm schick das an ⟦1⟧"]


def test_protect_sees_the_text_after_the_dictionary_and_the_press_enter_split():
    protect, calls = _protect()
    result = process_text(
        "my addr press enter",
        _config(press_enter=True),
        [DictionaryEntry("addr", "address")],
        translate_fn=no_translation,
        cleanup_fn=no_cleanup,
        protect_fn=protect,
    )
    assert calls["protect"] == ["my address"]
    assert (result.typed, result.press_enter) == ("Hauptstrasse 1", True)


def test_voice_commands_cannot_touch_a_protected_phrase():
    # "new line" inside the shielded phrase is not turned into a line break; the one outside still is
    protect, _ = _protect(trigger="the new line sign", token="⟦1⟧", expansion="<new line>")
    result = _run_protected("the new line sign new line done", protect)
    assert result.typed == "<new line>\ndone"


def test_a_phone_dictation_is_protected_and_restored_too():
    protect, calls = _protect()
    result = _run_protected("write to my address", protect, remote=True)
    assert calls["protect"] == ["write to my address"]
    assert (result.typed, result.saved) == ("write to Hauptstrasse 1", "write to Hauptstrasse 1")


def test_a_phone_dictation_through_translation_is_restored():
    protect, _ = _protect()

    def translate_fn(text, _c):
        return TranslateResult(text.replace("schreib an", "write to"), translated=True, source="de")

    result = _run_protected("schreib an my address", protect, remote=True, translate_fn=translate_fn)
    assert result.typed == "write to Hauptstrasse 1"
    assert result.translation.translated is True


def test_a_text_the_extension_leaves_alone_goes_through_unchanged_and_stays_learnable():
    protect, calls = _protect()
    result = _run_protected("hello there", protect)
    assert (result.typed, result.learnable, calls["restore"]) == ("hello there", True, [])


def test_a_failing_protect_types_the_dictation_unprotected(caplog):
    def boom(_text):
        raise RuntimeError("nope")

    result = _run_protected("hello there my address", boom)
    assert (result.typed, result.learnable) == ("hello there my address", True)
    assert "protect step failed" in caplog.text


def test_a_restore_that_gives_up_processes_the_dictation_again_without_protect():
    # The translator drops the token: the extension's restore answers None, so nothing half-restored is typed.
    protect, calls = _protect()
    cleanups = []

    def cleanup_fn(text, _c, _l):
        cleanups.append(text)
        return CleanupResult(text)

    def translate_fn(text, _c):
        return TranslateResult(text.replace("⟦1⟧", "").strip(), translated=True, source="de")

    result = _run_protected("schick an my address", protect, translate_fn=translate_fn, cleanup_fn=cleanup_fn)
    assert cleanups == ["schick an ⟦1⟧", "schick an my address"]  # the second pass is unprotected
    assert "⟦1⟧" not in result.typed and "⟦1⟧" not in result.saved
    assert len(calls["protect"]) == 1  # protect is not asked a second time


def test_a_raising_restore_processes_the_dictation_again_without_protect():
    def protect(text):
        def restore(_out):
            raise RuntimeError("nope")

        return text.replace("my address", "⟦1⟧"), restore

    result = _run_protected("write to my address", protect)
    assert (result.typed, result.saved) == ("write to my address", "write to my address")


def test_protect_is_not_called_for_an_enter_only_dictation():
    protect, calls = _protect()
    result = process_text(
        "press enter",
        _config(press_enter=True),
        [],
        translate_fn=no_translation,
        cleanup_fn=no_cleanup,
        protect_fn=protect,
    )
    assert (result.typed, result.press_enter, calls["protect"]) == ("", True, [])


def test_extend_fn_sees_the_restored_text():
    protect, _ = _protect()
    result = process_text(
        "mail my address",
        _config(),
        [],
        translate_fn=no_translation,
        cleanup_fn=no_cleanup,
        extend_fn=lambda text: text.upper(),
        protect_fn=protect,
    )
    assert (result.typed, result.saved) == ("MAIL HAUPTSTRASSE 1", "mail Hauptstrasse 1")
