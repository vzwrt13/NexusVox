"""The plain records of the vocabulary tables: dictionary entries and polish presets.

They live here, not next to the code that applies them, so the database needs nothing from `dictation/`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..snippets import MAX_NAME_WORDS

# A spoken name is matched on its first words only (`snippets.best_option`); a longer name never matches in full.
MAX_PRESET_NAME_WORDS = MAX_NAME_WORDS


@dataclass(frozen=True)
class DictionaryEntry:
    wrong: str
    right: str


@dataclass(frozen=True)
class PolishPreset:
    name: str
    instruction: str
    is_default: bool = False


def normalize_phrase(phrase: str) -> str:
    return " ".join(phrase.split())


def usage_key(wrong: str) -> str:
    """The key under which an entry's applications are counted: its ``wrong`` phrase, whitespace and case folded."""
    return normalize_phrase(wrong).lower()
