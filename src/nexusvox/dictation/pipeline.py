"""The text pipeline: from a raw transcript to what is typed and what is saved.

Commands (nexus, assistant, os-control) are decided before this runs; everything
here is the path of a dictation that gets typed. The steps, in order:

1. correction dictionary on the raw transcript
2. a trailing "press enter" is split off (not for the phone, which inserts the text itself)
3. extensions' `protect` hooks shield parts of the text (phone included)
4. voice commands (new line, symbols, numbers, ...)
5. auto cleanup through the [llm] model (`[cleanup].level`), for typing only
6. translation into English for typing only
7. the `protect` restores run on the text to type and on the text to save
8. extensions' `on_dictation` hooks change the final text (not for the phone: there is no active app)

The result says what to type, what to save (as spoken, before cleanup and translation),
whether Enter follows the paste, and what learning from the user's edits needs: whether
the typed words are still the spoken ones (not after cleanup or translation), and the
text the dictionary saw, which a learned entry must match to ever apply.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import CleanupConfig, Config, LlmConfig, TranslatorConfig, VoiceCommandsConfig
from ..storage.vocabulary import DictionaryEntry
from ..system.logsetup import Text
from .cleanup import MIN_WORDS, CleanupResult, cleanup
from .dictionary import apply_dictionary_counted
from .translator import TranslateResult, translate
from .voice_commands import process_voice_commands, split_press_enter

logger = logging.getLogger("nexusvox.pipeline")

Translate = Callable[[str, TranslatorConfig], TranslateResult]
Cleanup = Callable[[str, CleanupConfig, LlmConfig], CleanupResult]
Extend = Callable[[str], str]  # the extensions' on_dictation chain, already bound to the active app
Restore = Callable[[str], str | None]  # undoes a protection; None: it cannot (the text is processed again)
Protect = Callable[[str], tuple[str, Restore] | None]  # the extensions' protect chain; None: nothing to protect


@dataclass(frozen=True)
class ProcessedText:
    typed: str  # what goes to the cursor (or back to the phone)
    saved: str  # the transcript to store, as spoken
    press_enter: bool  # press Enter after the paste
    cleanup: CleanupResult  # what auto cleanup did
    translation: TranslateResult  # what the translator did (saved with the transcript)
    learnable: bool  # `typed` holds the spoken words (voice commands aside), not cleaned or translated
    spoken: str  # the text after the dictionary: a learned entry must match it
    # How often each dictionary entry replaced something.
    dictionary_hits: dict[DictionaryEntry, int] = field(default_factory=dict)


def _voice_commands(text: str, config: VoiceCommandsConfig) -> str:
    if not config.enabled:
        return text
    return process_voice_commands(
        text,
        frozenset() if config.bypass_symbols else frozenset(config.symbols),
        config.numbers_as_digits,
        frozenset() if config.bypass_structural else frozenset(config.structural),
    )


def _protect(text: str, protect_fn: Protect | None) -> tuple[str, Restore | None]:
    """*text* as the extensions' `protect` leaves it, and the function that undoes it; unchanged if they fail."""
    if protect_fn is None or not text.strip():  # an Enter-only dictation has nothing to protect
        return text, None
    try:
        answer = protect_fn(text)
    except Exception:
        logger.exception("The extension protect step failed; processing the text unprotected")
        return text, None
    return (text, None) if answer is None else answer


def _restored(restore: Restore, typed: str, saved: str) -> tuple[str, str] | None:
    """The text to type and the text to save with the protection undone, or None when it cannot be."""
    try:
        restored_typed, restored_saved = restore(typed), restore(saved)
    except Exception:
        logger.exception("The extension restore step failed")
        return None
    if restored_typed is None or restored_saved is None:
        return None
    return restored_typed, restored_saved


def process_text(
    raw: str,
    config: Config,
    dictionary: list[DictionaryEntry],
    *,
    remote: bool = False,
    translate_fn: Translate = translate,
    cleanup_fn: Cleanup = cleanup,
    extend_fn: Extend | None = None,
    protect_fn: Protect | None = None,
) -> ProcessedText:
    """Run the pipeline. Blocking (cleanup and the translator are HTTP), so callers run it in an executor."""
    corrected, dictionary_hits = apply_dictionary_counted(raw, dictionary)
    if corrected != raw:
        logger.info("Dictionary applied: %r -> %r", Text(raw), Text(corrected))

    press_enter = False
    if config.voice_commands.enabled and config.voice_commands.press_enter and not remote:
        corrected, press_enter = split_press_enter(corrected)

    protected, restore = _protect(corrected, protect_fn)
    processed = _voice_commands(protected, config.voice_commands)

    # Cleanup keeps every line break or is not used (cleanup.plausible).
    cleaned = cleanup_fn(processed, config.cleanup, config.llm)
    if config.cleanup.level == "off":
        logger.debug("Cleanup: off, typing the dictation as it is")
    elif cleaned.status is None:  # the model was not asked: every other outcome logs its own line
        logger.info("Cleanup (%s): skipped, the dictation is shorter than %d words", config.cleanup.level, MIN_WORDS)

    translation = translate_fn(cleaned.text, config.translator)
    typed, saved = translation.text, processed
    if restore is not None:
        restored = _restored(restore, typed, saved)
        if restored is None:
            # Never type a half-restored text: do it all again, unprotected (cleanup and translation run twice).
            logger.warning(
                "An extension could not restore its protected text; processing the dictation again without it"
            )
            return process_text(
                raw,
                config,
                dictionary,
                remote=remote,
                translate_fn=translate_fn,
                cleanup_fn=cleanup_fn,
                extend_fn=extend_fn,
            )
        typed, saved = restored
    extended = typed
    if extend_fn is not None and typed:  # an Enter-only dictation has no text for an extension to change
        try:
            extended = extend_fn(typed)
        except Exception:
            logger.exception("The extension step failed; typing the text unchanged by it")

    return ProcessedText(
        typed=extended,
        # A dictation that was only "press enter" leaves no text to save.
        saved="[press enter]" if press_enter and not saved else saved,
        press_enter=press_enter,
        cleanup=cleaned,
        translation=translation,
        # The translator passes English through byte for byte (translated=True all the same):
        # only a dictation whose words it actually changed is not learnable.
        learnable=not cleaned.cleaned and translation.text == cleaned.text and typed == translation.text == extended,
        spoken=corrected,
        dictionary_hits=dictionary_hits,
    )
