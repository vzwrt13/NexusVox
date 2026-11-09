"""Correction dictionary: replace words the model keeps getting wrong.

Entries map a phrase as the model writes it ("nexus fox") to the phrase the
user meant ("NexusVox"). Matching is case-insensitive and whole-word; the
replacement is inserted verbatim.
"""

from __future__ import annotations

import difflib
import re

from ..storage.vocabulary import DictionaryEntry, normalize_phrase


def apply_dictionary_counted(text: str, entries: list[DictionaryEntry]) -> tuple[str, dict[DictionaryEntry, int]]:
    """`apply_dictionary`, plus how often each entry replaced something (entries that never fired are left out)."""
    hits: dict[DictionaryEntry, int] = {}
    if not text or not entries:
        return text, hits
    # Every entry looks at the spoken text, never at another entry's replacement: "cloud code" -> "Claude Code"
    # and "claude" -> "Cloud" must not turn "Claude Code" into "Cloud Code".
    found: list[tuple[int, int, DictionaryEntry]] = []
    for entry in entries:
        wrong = normalize_phrase(entry.wrong)
        if not wrong:
            continue
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in wrong.split()) + r"(?!\w)"
        found.extend((m.start(), m.end(), entry) for m in re.finditer(pattern, text, flags=re.IGNORECASE))
    # Each span is replaced at most once: the longest match wins ("cloud code editor" over "cloud code"),
    # the earlier one on a tie.
    found.sort(key=lambda f: (-(f[1] - f[0]), f[0]))
    taken: list[tuple[int, int, DictionaryEntry]] = []
    for start, end, entry in found:
        if all(end <= t_start or start >= t_end for t_start, t_end, _ in taken):
            taken.append((start, end, entry))
    out: list[str] = []
    pos = 0
    for start, end, entry in sorted(taken, key=lambda t: t[0]):
        out.append(text[pos:start])
        out.append(entry.right)
        pos = end
        hits[entry] = hits.get(entry, 0) + 1
    out.append(text[pos:])
    return "".join(out), hits


def apply_dictionary(text: str, entries: list[DictionaryEntry]) -> str:
    """Replace every whole-word, case-insensitive occurrence of each entry's ``wrong`` phrase."""
    return apply_dictionary_counted(text, entries)[0]


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
