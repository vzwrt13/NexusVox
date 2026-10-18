"""Polish presets: named instructions the user keeps in the dashboard.

Saying a preset's name as the polish instruction ("prompt engineer") uses its stored
instruction; words after the name are added to it ("prompt engineer, and keep it
short"). The default preset applies when the polish key is tapped without a word.
Names are the user's own, in their own language, so the bilingual rule is met by the
user, as with snippets. Pure, so the database and the dashboard can import it anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from .snippets import MAX_NAME_WORDS, SnippetOption, best_option

# A spoken name is matched on its first words only (`snippets.best_option`); a longer name never matches in full.
MAX_PRESET_NAME_WORDS = MAX_NAME_WORDS


@dataclass(frozen=True)
class PolishPreset:
    name: str
    instruction: str
    is_default: bool = False


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
