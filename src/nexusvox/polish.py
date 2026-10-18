"""Polish: rewrite the selected text by a spoken instruction.

The user selects text, holds the hotkey, taps `[hotkey].polish_key` and says what
to do ("make it shorter", "mach es formeller") or nothing. The instruction - or,
when nothing was said, the default polish instruction - goes to the language model
with the selection; the reply replaces the selection. The prompts live here so they
are versioned and tested with the code.
"""

from __future__ import annotations

import re

from .config import LlmConfig
from .llm import complete

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

_FENCE_RE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)


def build_user_message(selection: str, instruction: str) -> str:
    return f"Instruction: {instruction.strip() or DEFAULT_INSTRUCTION}\n\nText:\n{selection}"


def clean_reply(reply: str) -> str:
    """Strip a code fence the model wrapped around its answer despite being told not to."""
    reply = reply.strip()
    m = _FENCE_RE.match(reply)
    return m.group(1).strip() if m else reply


def polish(selection: str, instruction: str, config: LlmConfig) -> str:
    """The selection rewritten by the instruction. Raises `llm.LlmError`."""
    return clean_reply(complete(config, SYSTEM_PROMPT, build_user_message(selection, instruction)))
