"""Tests for polish presets: resolving a spoken instruction, and storing presets."""

from __future__ import annotations

from nexusvox.polish_presets import PolishPreset, resolve_instruction

PROMPT = PolishPreset("prompt engineer", "Rewrite this as a precise prompt for an AI assistant.")
FORMAL = PolishPreset("formelle Mail", "Schreib das als formelle E-Mail.", is_default=True)
PRESETS = [PROMPT, FORMAL]


def test_nothing_said_uses_the_default_preset():
    assert resolve_instruction("", PRESETS) == (FORMAL.instruction, "formelle Mail")


def test_nothing_said_without_a_default_is_the_built_in_instruction():
    assert resolve_instruction("  ", [PROMPT]) == ("", None)


def test_a_spoken_preset_name_uses_its_instruction():
    assert resolve_instruction("Prompt engineer.", PRESETS) == (PROMPT.instruction, "prompt engineer")


def test_close_spellings_and_german_names_match():
    assert resolve_instruction("prompt-engineer", PRESETS)[1] == "prompt engineer"
    assert resolve_instruction("Formelle Mail", PRESETS)[1] == "formelle Mail"


def test_words_after_the_name_are_added():
    instruction, name = resolve_instruction("formelle Mail, und halte es kurz", PRESETS)
    assert name == "formelle Mail"
    assert instruction == "Schreib das als formelle E-Mail.\nAlso: und halte es kurz"


def test_anything_else_is_the_instruction_as_spoken():
    assert resolve_instruction("make it shorter", PRESETS) == ("make it shorter", None)
    assert resolve_instruction("mach es kürzer", PRESETS) == ("mach es kürzer", None)


# -- storage ----------------------------------------------------------------------


def test_presets_roundtrip_and_one_default(db):
    first = db.save_polish_preset(None, PolishPreset(" prompt   engineer ", " Rewrite it. ", is_default=True))
    assert first["name"] == "prompt engineer" and first["instruction"] == "Rewrite it." and first["is_default"]
    second = db.save_polish_preset(None, FORMAL)
    assert [p["is_default"] for p in db.list_polish_presets()] == [True, False]  # sorted: formelle, prompt
    assert db.list_polish_presets()[0]["id"] == second["id"]
    assert sorted(p.name for p in db.get_polish_presets()) == ["formelle Mail", "prompt engineer"]


def test_presets_need_a_unique_name_and_an_instruction(db):
    saved = db.save_polish_preset(None, PROMPT)
    assert db.save_polish_preset(None, PolishPreset("", "x")) is None
    assert db.save_polish_preset(None, PolishPreset("short", "  ")) is None
    assert db.save_polish_preset(None, PolishPreset("PROMPT ENGINEER", "other")) is None
    assert db.save_polish_preset(999, PolishPreset("other", "x")) is None
    edited = db.save_polish_preset(saved["id"], PolishPreset("Prompt Engineer", "New text."))
    assert edited == {"id": saved["id"], "name": "Prompt Engineer", "instruction": "New text.", "is_default": False}
    assert db.delete_polish_preset(saved["id"]) and not db.delete_polish_preset(saved["id"])
