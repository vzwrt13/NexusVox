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

Expansions must reach the cursor exactly as stored, so the pipeline works in two
steps: `mark_snippets` swaps each match for a private-use placeholder character
before voice commands and translation run, and `expand_snippets` puts the stored
text back in right before injection. A translator that drops a placeholder gets
its result discarded by the caller (`placeholders_intact`).

`resolve_open_target` serves "nexus open <snippet>": the whole phrase must be one
snippet match, and its text is what gets opened.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# One Unicode private-use character per expansion; voice commands and the dictionary ignore them.
_PLACEHOLDER_BASE = 0xE000
_MAX_PLACEHOLDERS = 0x1900
# How many words after a trigger may form an option name.
_MAX_NAME_WORDS = 5
# Minimum similarity between the spoken name and an option name.
MIN_OPTION_SCORE = 0.75

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_TRAILING_MARK_RE = re.compile(r"[.!?]+\s*$")


@dataclass(frozen=True)
class Snippet:
    trigger: str
    expansion: str = ""
    options: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _normalize(phrase: str) -> str:
    return " ".join(phrase.split())


def _squash(phrase: str) -> str:
    """Lower-case letters and digits only: "Personal Tooling" and "personal-tooling" compare equal."""
    return "".join(ch for ch in phrase.lower() if ch.isalnum())


def _trigger_pattern(trigger: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in trigger.split()) + r"(?!\w)", re.IGNORECASE)


def best_option(spoken: str, options: tuple[tuple[str, str], ...]) -> tuple[str, int] | None:
    """The option text for the leading words of *spoken*, and how many characters they span.

    Tries the first 1 to `_MAX_NAME_WORDS` words against every option name and
    keeps the best score at or above `MIN_OPTION_SCORE`; on a tie the longer
    span wins, so "nexus vox" beats "nexus".
    """
    words = list(_WORD_RE.finditer(spoken))
    best: tuple[float, int, str] | None = None
    for count in range(1, min(len(words), _MAX_NAME_WORDS) + 1):
        span_end = words[count - 1].end()
        heard = _squash(spoken[:span_end])
        for name, text in options:
            score = difflib.SequenceMatcher(a=heard, b=_squash(name), autojunk=False).ratio()
            if score >= MIN_OPTION_SCORE and (best is None or (score, span_end) > best[:2]):
                best = (score, span_end, text)
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
    if not text or not snippets:
        return text, expansions
    for snippet in sorted(snippets, key=lambda s: len(_normalize(s.trigger)), reverse=True):
        if not _normalize(snippet.trigger):
            continue
        out: list[str] = []
        pos = 0
        for start, end, replacement in _matches(text, snippet):
            if len(expansions) >= _MAX_PLACEHOLDERS:
                break
            expansions.append(replacement)
            out.append(text[pos:start])
            out.append(chr(_PLACEHOLDER_BASE + len(expansions) - 1))
            pos = end
        out.append(text[pos:])
        text = "".join(out)
    return text, expansions


def resolve_open_target(phrase: str, snippets: list[Snippet]) -> str | None:
    """The text of the one snippet match that makes up all of *phrase*, or None."""
    marked, expansions = mark_snippets(phrase.strip(), snippets)
    if len(expansions) == 1 and marked.strip(" .,!?") == chr(_PLACEHOLDER_BASE):
        return expansions[0].strip()
    return None


# Spoken verbs for "nexus open <snippet>", English and German.
_OPEN_VERBS = ("open", "öffne", "öffnen", "öffnet")
_OPEN_VERB_RE = re.compile(r"^(?:" + "|".join(_OPEN_VERBS) + r")\b[\s,:]*", re.IGNORECASE)
_NEXUS_RE = re.compile(r"^\s*nexus\b[\s,:]*(.+?)\s*$", re.IGNORECASE | re.DOTALL)


def resolve_nexus_open(text: str, snippets: list[Snippet]) -> str | None:
    """What "nexus open <snippet>" / "nexus öffne <snippet>" should open, or None.

    The verb may be part of the trigger ("open remote repo") or said on its own
    in either language ("nexus öffne remote repo nexusvox" with the trigger
    "open remote repo"); a trigger's own leading verb is ignored in that case.
    """
    m = _NEXUS_RE.match(text)
    if m is None or not snippets:
        return None
    rest = m.group(1)
    verb = _OPEN_VERB_RE.match(rest)
    if verb is None:
        return None
    target = resolve_open_target(rest, snippets)
    if target is not None:
        return target
    bare = [
        Snippet(_OPEN_VERB_RE.sub("", _normalize(s.trigger)), s.expansion, s.options)
        for s in snippets
        if _OPEN_VERB_RE.sub("", _normalize(s.trigger))
    ]
    return resolve_open_target(rest[verb.end() :], bare)


def placeholders_intact(text: str, expansions: list[str]) -> bool:
    """True when every placeholder for *expansions* is still in *text* exactly once."""
    return all(text.count(chr(_PLACEHOLDER_BASE + i)) == 1 for i in range(len(expansions)))


def expand_snippets(text: str, expansions: list[str]) -> str:
    """Put the stored expansions back in place of their placeholders."""
    for i, expansion in enumerate(expansions):
        text = text.replace(chr(_PLACEHOLDER_BASE + i), expansion)
    return text


def parse_options(lines: str) -> tuple[tuple[str, str], ...]:
    """Options from the dashboard's text box: one ``name = text`` per line, blank lines ignored."""
    options: list[tuple[str, str]] = []
    for line in lines.splitlines():
        name, sep, text = line.partition("=")
        if sep and name.strip() and text.strip():
            options.append((_normalize(name), text.strip()))
    return tuple(options)
