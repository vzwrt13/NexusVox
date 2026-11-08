"""Polish presets: named instructions the user keeps in the dashboard.

Saying a preset's name as the polish instruction ("prompt engineer") uses its stored
instruction; words after the name are added to it ("prompt engineer, and keep it
short"). The default preset applies when the polish key is tapped without a word.
A spoken instruction in a script nobody here writes is a mishearing (`unexpected_script`), unless it names a preset.
Names are the user's own, in their own language, so the bilingual rule is met by the
user, as with snippets.
"""

from __future__ import annotations

import unicodedata
from collections import Counter

from ..snippets import SnippetOption, best_option
from ..storage.vocabulary import PolishPreset


def _script(ch: str) -> str:
    """The script of a letter: "LATIN", "CYRILLIC", "CJK", "KATAKANA", ...

    Latin-1 and Latin Extended-A/B (up to U+024F) are Latin without a lookup: their names are not
    script names (``ª`` is "FEMININE ORDINAL INDICATOR", ``µ`` "MICRO SIGN"). Compatibility forms
    (Kelvin sign, fullwidth letters) are folded first. Anything else is the first word of its Unicode
    name, with the Katakana-Hiragana mark counted as Katakana.
    """
    if ord(ch) < 0x250:
        return "LATIN"
    ch = unicodedata.normalize("NFKC", ch)[:1]
    if ord(ch) < 0x250:
        return "LATIN"
    try:
        word = unicodedata.name(ch).split()[0]
    except ValueError:
        return "UNKNOWN"
    return "KATAKANA" if word.startswith("KATAKANA") else word


def unexpected_script(spoken: str, presets: list[PolishPreset]) -> str | None:
    """The script of *spoken* when most of its letters are in one that is neither Latin nor a preset's, else ``None``.

    The speech model detects the language itself and no language is sent to it, so a short
    instruction ("Brief") can come back in another script ("Бриф."). NexusVox speaks English and
    German, so Latin is expected. A foreign script is accepted only when the words are a saved
    preset's name (`resolve_instruction` finds it): the names are the user's own words, but a
    Cyrillic preset does not make every other Cyrillic text an instruction.
    """
    counts = Counter(_script(ch) for ch in spoken if ch.isalpha())
    foreign = {script: n for script, n in counts.items() if script != "LATIN"}
    if sum(foreign.values()) * 2 <= sum(counts.values()):
        return None
    if resolve_instruction(spoken, presets)[1] is not None:
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
