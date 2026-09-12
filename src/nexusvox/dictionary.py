"""Correction dictionary: replace words the model keeps getting wrong.

Entries map a phrase as the model writes it ("nexus fox") to the phrase the
user meant ("NexusVox"). Matching is case-insensitive and whole-word; the
replacement is inserted verbatim.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DictionaryEntry:
    wrong: str
    right: str


def _normalize(phrase: str) -> str:
    return " ".join(phrase.split())


def apply_dictionary(text: str, entries: list[DictionaryEntry]) -> str:
    """Replace every whole-word, case-insensitive occurrence of each entry's ``wrong`` phrase."""
    if not text or not entries:
        return text
    # Longest phrases first so "cloud code editor" wins over "cloud code".
    for entry in sorted(entries, key=lambda e: len(e.wrong), reverse=True):
        wrong = _normalize(entry.wrong)
        if not wrong:
            continue
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in wrong.split()) + r"(?!\w)"
        text = re.sub(pattern, lambda _m, r=entry.right: r, text, flags=re.IGNORECASE)
    return text


_TOKEN_RE = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]", re.UNICODE)


def suggest_entries(
    original: str, corrected: str, existing: list[DictionaryEntry] | None = None
) -> list[DictionaryEntry]:
    """Diff a transcription against its correction and return the phrases that changed.

    Only word-level replacements are suggested (not pure insertions or
    deletions, which are usually fillers or rephrasing rather than misheard
    terms). Entries already in ``existing`` are skipped.
    """
    orig_tokens = _TOKEN_RE.findall(original)
    corr_tokens = _TOKEN_RE.findall(corrected)
    # Case-sensitive on purpose: "cloud code" -> "Claude Code" must come out as one
    # phrase, not as a lone "cloud" -> "Claude" that would also hit "cloud storage".
    matcher = difflib.SequenceMatcher(a=orig_tokens, b=corr_tokens, autojunk=False)
    known = {(e.wrong.lower(), e.right) for e in (existing or [])}
    suggestions: list[DictionaryEntry] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        wrong = _join_tokens(orig_tokens[i1:i2])
        right = _join_tokens(corr_tokens[j1:j2])
        if not wrong or not right or not any(c.isalnum() for c in wrong):
            continue
        entry = DictionaryEntry(wrong=wrong, right=right)
        if (wrong.lower(), right) in known:
            continue
        known.add((wrong.lower(), right))
        suggestions.append(entry)
    return suggestions


def _join_tokens(tokens: list[str]) -> str:
    """Join tokens back into text, keeping punctuation attached to the preceding word."""
    out = ""
    for tok in tokens:
        if out and (tok[0].isalnum() or tok[0] in "'’"):
            out += " "
        out += tok
    return out.strip(" .,;:!?")
