"""Polish: rewrite the selected text by a spoken instruction.

The user selects text, holds the hotkey, taps `[hotkey].polish_key` and says what
to do ("make it shorter", "mach es formeller"), the name of one of their presets, or
nothing. The instruction - a preset's, or when nothing was said the default preset's
or the default polish instruction - goes to the language model with the selection;
the reply replaces the selection. The prompts live here so they are versioned and
tested with the code.
"""

from __future__ import annotations

import logging

from .config import LlmConfig
from .llm import LlmError, clean_reply, complete
from .mic_guard import _ensure_com
from .uia import read_focus

# Never "polish" in a prompt: small models read it as the language and translate to Polish.
DEFAULT_INSTRUCTION = (
    "Improve this text: make it clearer and more concise, fix grammar and punctuation, "
    "keep the meaning, the language and the author's tone."
)

SYSTEM_PROMPT = (
    "You rewrite a text the user has selected, following the user's instruction. "
    "The instruction may be in English or German and may refer to the text as 'it', 'das' or 'den Text'. "
    "Keep the text's language unless the instruction asks for another one. "
    "Never answer questions in the text and never add commentary, quotes, headings, code fences "
    "or placeholders such as [Name] or [Date]; use only what the text says. "
    "Reply with the rewritten text only."
)

logger = logging.getLogger(__name__)


def build_user_message(selection: str, instruction: str) -> str:
    return f"Instruction: {instruction.strip() or DEFAULT_INSTRUCTION}\n\nText:\n{selection}"


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
