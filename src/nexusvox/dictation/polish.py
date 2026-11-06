"""Polish: rewrite the selected text by a spoken instruction.

The user selects text, holds the hotkey, taps `[hotkey].polish_key` and says what
to do ("make it shorter", "mach es formeller"), the name of one of their presets, or
nothing. The instruction - a preset's, or when nothing was said the default preset's
or the default polish instruction - goes to the language model with the selection;
the reply replaces the selection. The prompts live here so they are versioned and
tested with the code.
"""

from __future__ import annotations

import functools
import logging

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

from ..audio.mic_guard import _ensure_com
from ..config import LlmConfig
from ..llm import LlmError, clean_reply, complete
from ..system.uia import read_focus

# Never "polish" in a prompt: small models read it as the language and translate to Polish.
DEFAULT_INSTRUCTION = (
    "Improve this text: make it clearer and more concise, fix grammar and punctuation, "
    "keep the meaning, the language and the author's tone."
)

SYSTEM_PROMPT = (
    "You rewrite a text the user has selected, following the user's instruction. "
    "The instruction may be in English or German and may refer to the text as 'it', 'das' or 'den Text'. "
    "Keep the text's language, also when the instruction is in another language; only an explicit request "
    "for another language (a translation) overrides that. "
    "Never answer questions in the text and never add commentary, quotes, headings, code fences "
    "or placeholders such as [Name] or [Date]; use only what the text says. "
    "Reply with the rewritten text only."
)

logger = logging.getLogger("nexusvox.polish")


# Detection looks at the start of the selection only (a 100,000-character selection must not delay the model)
# and knows more than English and German, so a Dutch or French text is not mistaken for one of the two.
_DETECT_CHARS = 2000
_MIN_CONFIDENCE = 0.7


@functools.lru_cache(maxsize=1)
def _detector() -> LanguageDetector:
    languages = [
        Language.ENGLISH, Language.GERMAN, Language.FRENCH, Language.SPANISH, Language.ITALIAN,
        Language.DUTCH, Language.PORTUGUESE, Language.POLISH, Language.TURKISH, Language.SWEDISH,
    ]  # fmt: skip
    return LanguageDetectorBuilder.from_languages(*languages).build()


def detect_selection_language(selection: str) -> str | None:
    """The English name of the language the selection is written in, None when unsure (also for a mix)."""
    values = _detector().compute_language_confidence_values(selection[:_DETECT_CHARS])
    if not values or values[0].value < _MIN_CONFIDENCE:
        return None
    return values[0].language.name.capitalize()


def language_line(selection: str) -> str:
    """The prompt line that names the selection's language, "" when it is unsure.

    The language comes from the selected text, never from the spoken instruction: a German instruction on
    English text must still give English back. Only an explicit request for another language overrides it."""
    name = detect_selection_language(selection)
    if name is None:
        return ""
    return (
        f"Language of the text: {name}. If the instruction asks to translate or to switch language, follow it. "
        f"If it does not, the reply must stay in {name}, even when the instruction is written in another language."
    )


def build_user_message(selection: str, instruction: str) -> str:
    instruction = instruction.strip() or DEFAULT_INSTRUCTION
    line = language_line(selection)
    header = f"Instruction: {instruction}" + (f"\n{line}" if line else "")
    return f"{header}\n\nText:\n{selection}"


def polish(selection: str, instruction: str, config: LlmConfig) -> str:
    """The selection rewritten by the instruction, with the selection's own leading and
    trailing whitespace kept (a selected line's newline must survive). Raises `llm.LlmError`."""
    rewritten = clean_reply(complete(config, SYSTEM_PROMPT, build_user_message(selection, instruction)))
    if not rewritten:
        raise LlmError("the model returned no text")
    core = selection.strip()
    start = selection.find(core)
    return selection[:start] + rewritten + selection[start + len(core) :]


def read_selection(injection_delay_ms: int, in_terminals: bool = False) -> str:
    """The selected text in the focused app, "" when there is none or the focus is a
    terminal and *in_terminals* is off.

    Asks the control through UI Automation first, which sends no key and leaves the
    clipboard alone. Only a control that does not report its selection gets Ctrl+C - a
    terminal only with *in_terminals*, because there Ctrl+C without a selection
    interrupts the running program.
    """
    from .injector import copy_selection  # Win32 at import; polish itself stays importable anywhere

    _ensure_com()
    focus = read_focus()
    if focus.terminal and not in_terminals:
        logger.info("Polish: the focus is a terminal, not sending Ctrl+C ([polish].in_terminals is off)")
        return ""
    if focus.selection is not None:
        return focus.selection
    return copy_selection(injection_delay_ms)
