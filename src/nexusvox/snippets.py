"""Snippets: a spoken trigger phrase expands into a stored text.

The user defines every snippet in the dashboard - a trigger ("my address",
"meine Signatur") and an expansion of any length, line breaks included. A
trigger matches anywhere in the dictation, whole-word and case-insensitive, in
whatever language the user wrote it. No snippets ship with NexusVox.

A snippet can also carry *options*, name -> text pairs ("NexusVox" ->
"https://github.com/..."). The words after its trigger then pick the option
whose name matches best ("open remote repo nexus box" -> the NexusVox URL);
spaces, dashes and case do not count and small mishearings are tolerated. When
no option is close enough, the plain expansion is used, or the trigger stays
text if there is none.

Expansions must reach the cursor exactly as stored, so the text pipeline works
in two steps: `mark_snippets` swaps each match for a private-use placeholder
character before voice commands and translation run, and `expand_snippets`
puts the stored text back in right before injection.

`resolve_open_target` serves "nexus open <snippet>" (the grammar lives in
`os_commands`): the spoken phrase must be exactly one snippet match.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import NamedTuple

from .os_commands import strip_open_verb

# One Unicode private-use character per expansion; voice commands and the dictionary ignore them.
_PLACEHOLDER_BASE = 0xE000
# How many words after a trigger may form an option name.
MAX_NAME_WORDS = 5
# Minimum similarity between the spoken name and an option name.
MIN_OPTION_SCORE = 0.75

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_TRAILING_MARK_RE = re.compile(r"[.!?]+\s*$")


class SnippetOption(NamedTuple):
    name: str
    text: str


@dataclass(frozen=True)
class Snippet:
    trigger: str
    expansion: str = ""
    options: tuple[SnippetOption, ...] = ()


def _normalize(phrase: str) -> str:
    return " ".join(phrase.split())


def _squash(phrase: str) -> str:
    """Lower-case letters and digits only: "Personal Tooling" and "personal-tooling" compare equal."""
    return "".join(ch for ch in phrase.lower() if ch.isalnum())


def _trigger_pattern(trigger: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in trigger.split()) + r"(?!\w)", re.IGNORECASE)


def best_option(spoken: str, options: tuple[SnippetOption, ...]) -> tuple[str, int] | None:
    """The option text for the leading words of *spoken*, and how many characters they span.

    Tries the first 1 to `MAX_NAME_WORDS` words against every option name and
    keeps the best score at or above `MIN_OPTION_SCORE`; on a tie the longer
    span wins, so "nexus vox site" picks `nexusvox-site` over `NexusVox`.
    """
    words = list(_WORD_RE.finditer(spoken))
    best: tuple[float, int, str] | None = None
    for count in range(1, min(len(words), MAX_NAME_WORDS) + 1):
        span_end = words[count - 1].end()
        heard = _squash(spoken[:span_end])
        for option in options:
            score = difflib.SequenceMatcher(a=heard, b=_squash(option.name), autojunk=False).ratio()
            if score >= MIN_OPTION_SCORE and (best is None or (score, span_end) > best[:2]):
                best = (score, span_end, option.text)
    return (best[2], best[1]) if best else None


def _matches(text: str, snippet: Snippet) -> list[tuple[int, int, str]]:
    """(start, end, replacement) for every match of *snippet* in *text*, left to right."""
    found: list[tuple[int, int, str]] = []
    for m in _trigger_pattern(_normalize(snippet.trigger)).finditer(text):
        end, replacement = m.end(), snippet.expansion
        if snippet.options:
            rest = text[end:]
            lead = len(rest) - len(rest.lstrip(" ,:;"))
            option = best_option(rest[lead:], snippet.options)
            if option is not None:
                replacement, span = option
                end += lead + span
        if not replacement:
            continue
        mark = _TRAILING_MARK_RE.match(text, end)
        if mark is not None:
            end = mark.end()
        found.append((m.start(), end, replacement))
    return found


def mark_snippets(text: str, snippets: list[Snippet]) -> tuple[str, list[str]]:
    """Replace every snippet match in *text* with a placeholder; returns the text and the expansions in order.

    Longest trigger first, so "my work address" wins over "my address". A
    sentence mark right after a match at the end of the dictation is dropped
    ("My address." -> the address, without a trailing period).
    """
    expansions: list[str] = []
    for snippet in sorted(snippets, key=lambda s: len(_normalize(s.trigger)), reverse=True):
        if not _normalize(snippet.trigger):
            continue
        out: list[str] = []
        pos = 0
        for start, end, replacement in _matches(text, snippet):
            expansions.append(replacement)
            out.append(text[pos:start])
            out.append(chr(_PLACEHOLDER_BASE + len(expansions) - 1))
            pos = end
        out.append(text[pos:])
        text = "".join(out)
    return text, expansions


def resolve_open_target(spoken: str, snippets: list[Snippet]) -> str | None:
    """The text "nexus open <spoken>" should open, or None.

    *spoken* is what follows the open verb. Each trigger is compared without its
    own leading open verb, so the trigger "open remote repo" answers both
    "nexus open remote repo …" and "nexus öffne remote repo …".
    """
    bare = [Snippet(strip_open_verb(s.trigger), s.expansion, s.options) for s in snippets]
    marked, expansions = mark_snippets(spoken.strip(), bare)
    if len(expansions) == 1 and marked.strip(" .,!?") == chr(_PLACEHOLDER_BASE):
        return expansions[0].strip()
    return None


def placeholders_intact(text: str, expansions: list[str]) -> bool:
    """True when every placeholder for *expansions* is still in *text* exactly once."""
    return all(text.count(chr(_PLACEHOLDER_BASE + i)) == 1 for i in range(len(expansions)))


def expand_snippets(text: str, expansions: list[str]) -> str:
    """Put the stored expansions back in place of their placeholders."""
    for i, expansion in enumerate(expansions):
        text = text.replace(chr(_PLACEHOLDER_BASE + i), expansion)
    return text
