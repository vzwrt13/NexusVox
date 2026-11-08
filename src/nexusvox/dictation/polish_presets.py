"""Polish presets: named instructions the user keeps in the dashboard.

Saying a preset's name as the polish instruction ("prompt engineer") uses its stored
instruction; words after the name are added to it ("prompt engineer, and keep it
short"). The default preset applies when the polish key is tapped without a word.
A spoken instruction in a script nobody here writes is a mishearing (`unexpected_script`).
Names are the user's own, in their own language, so the bilingual rule is met by the
user, as with snippets.
"""

from __future__ import annotations

import unicodedata

from ..snippets import SnippetOption, best_option
from ..storage.vocabulary import PolishPreset


def _script(ch: str) -> str:
    """The script of a letter: the first word of its Unicode name ("LATIN", "CYRILLIC", "CJK", ...)."""
    try:
        return unicodedata.name(ch).split()[0]
    except ValueError:
        return "UNKNOWN"


def _letter_scripts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            counts[_script(ch)] = counts.get(_script(ch), 0) + 1
    return counts


def unexpected_script(spoken: str, presets: list[PolishPreset]) -> str | None:
    """The script of *spoken* when most of its letters are in one nobody here writes, else ``None``.

    The speech model detects the language itself and no language is sent to it, so a short
    instruction ("Brief") can come back in another script ("Бриф."). NexusVox speaks English and
    German, so Latin is expected, and so is any script the saved preset names use (they are the
    user's own words). Anything else is a mishearing, not an instruction.
    """
    counts = _letter_scripts(spoken)
    expected = {"LATIN"}.union(*(_letter_scripts(p.name).keys() for p in presets))
    foreign = {script: n for script, n in counts.items() if script not in expected}
    if sum(foreign.values()) * 2 <= sum(counts.values()):
        return None
    return max(foreign, key=lambda script: foreign[script])


def resolve_instruction(spoken: str, presets: list[PolishPreset]) -> tuple[str, str | None]:
    """The instruction for the model and the name of the preset it came from, if any.

    Nothing said: the default preset, or "" (polish then uses `polish.DEFAULT_INSTRUCTION`).
    A preset name at the start is matched like a snippet option (`snippets.best_option`: case,
    spaces and hyphens ignored, close spellings accepted) and the words after it are added to
    its instruction. Anything else is the instruction as spoken.
    """
    spoken = spoken.strip()
    if not spoken:
        default = next((p for p in presets if p.is_default), None)
        return (default.instruction, default.name) if default else ("", None)
    by_name = {p.name: p for p in presets}
    match = best_option(spoken, tuple(SnippetOption(p.name, p.name) for p in presets))
    if match is None:
        return spoken, None
    preset = by_name[match[0]]
    rest = spoken[match[1] :].strip(" ,.:;!?")
    return (f"{preset.instruction}\nAlso: {rest}" if rest else preset.instruction), preset.name
