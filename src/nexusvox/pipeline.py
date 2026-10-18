"""The text pipeline: from a raw transcript to what is typed and what is saved.

Commands (nexus, assistant, os-control) are decided before this runs; everything
here is the path of a dictation that gets typed. The steps, in order:

1. correction dictionary on the raw transcript
2. a trailing "press enter" is split off (not for the phone, which inserts the text itself)
3. snippet triggers become placeholders, so later steps never touch their stored text
4. voice commands (new line, symbols, numbers, ...)
5. translation into English for typing only; a translator that loses a placeholder is ignored
6. snippet placeholders are expanded again

The result says what to type, what to save (as spoken, before translation),
whether Enter follows the paste, and whether the typed text still equals the
spoken words - the only case in which learning from the user's edits can
produce a dictionary entry that will ever match.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config, TranslatorConfig, VoiceCommandsConfig
from .dictionary import DictionaryEntry, apply_dictionary
from .snippets import Snippet, expand_snippets, mark_snippets, placeholders_intact
from .translator import TranslateResult, translate
from .voice_commands import process_voice_commands, split_press_enter

logger = logging.getLogger(__name__)

Translate = Callable[[str, TranslatorConfig], TranslateResult]


@dataclass(frozen=True)
class ProcessedText:
    typed: str  # what goes to the cursor (or back to the phone)
    saved: str  # the transcript to store, as spoken
    press_enter: bool  # press Enter after the paste
    translation: TranslateResult  # what the translator did (saved with the transcript)
    learnable: bool  # `typed` is the spoken words after the dictionary, untouched by later steps


def _voice_commands(text: str, config: VoiceCommandsConfig) -> str:
    if not config.enabled:
        return text
    return process_voice_commands(
        text,
        frozenset() if config.bypass_symbols else frozenset(config.symbols),
        config.numbers_as_digits,
        frozenset() if config.bypass_structural else frozenset(config.structural),
    )


def process_text(
    raw: str,
    config: Config,
    dictionary: list[DictionaryEntry],
    snippets: list[Snippet],
    *,
    remote: bool = False,
    translate_fn: Translate = translate,
) -> ProcessedText:
    """Run the pipeline. Blocking (the translator is HTTP), so callers run it in an executor."""
    corrected = apply_dictionary(raw, dictionary)
    if corrected != raw:
        logger.info("Dictionary applied: %r -> %r", raw, corrected)

    press_enter = False
    if config.voice_commands.enabled and config.voice_commands.press_enter and not remote:
        corrected, press_enter = split_press_enter(corrected)

    marked, expansions = mark_snippets(corrected, snippets)
    if expansions:
        logger.info("Snippets matched: %d", len(expansions))

    processed = _voice_commands(marked, config.voice_commands)

    translation = translate_fn(processed, config.translator)
    if translation.translated and not placeholders_intact(translation.text, expansions):
        logger.warning("Translator dropped a snippet placeholder; typing the original text")
        translation = TranslateResult(processed, error="snippet placeholder lost")
    typed = expand_snippets(translation.text, expansions)
    saved = expand_snippets(processed, expansions)

    return ProcessedText(
        typed=typed,
        # A dictation that was only "press enter" leaves no text to save.
        saved="[press enter]" if press_enter and not saved else saved,
        press_enter=press_enter,
        translation=translation,
        learnable=not translation.translated and not press_enter and not expansions and processed == corrected,
    )
