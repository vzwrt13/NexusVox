"""Dashboard API: the correction dictionary and polish presets."""

from __future__ import annotations

from ..polish_presets import PolishPreset


class VocabularyMixin:
    """Dictionary entries, polish presets and the suggestions learned from edits."""

    # ---- Correction dictionary ---------------------------------------------

    def get_dictionary(self) -> dict:
        return {"entries": self._db.list_dictionary() if self._db else []}

    def add_dictionary_entry(self, wrong: str, right: str) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        entry = self._db.add_dictionary_entry(wrong, right)
        if entry is None:
            return {"ok": False, "error": "Both fields are required"}
        return {"ok": True, "entry": entry}

    def delete_dictionary_entry(self, entry_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_dictionary_entry(entry_id)}

    def get_dictionary_suggestions(self) -> dict:
        return {"suggestions": self._db.list_dictionary_suggestions() if self._db else []}

    def accept_dictionary_suggestion(self, suggestion_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        entry = self._db.accept_dictionary_suggestion(suggestion_id)
        if entry is None:
            return {"ok": False, "error": "Suggestion not found"}
        return {"ok": True, "entry": entry}

    def delete_dictionary_suggestion(self, suggestion_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_dictionary_suggestion(suggestion_id)}

    # ---- Polish presets ----------------------------------------------------

    def get_polish_presets(self) -> dict:
        return {"presets": self._db.list_polish_presets() if self._db else []}

    def save_polish_preset(self, name: str, instruction: str, is_default: bool, preset_id: int | None) -> dict:
        """Add a preset, or change the one with ``preset_id``. Read on every polish, so this is live."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        saved = self._db.save_polish_preset(preset_id, PolishPreset(str(name), str(instruction), bool(is_default)))
        if saved is None:
            return {"ok": False, "error": "A unique name of up to 5 words and an instruction are required"}
        return {"ok": True, "preset": saved}

    def delete_polish_preset(self, preset_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_polish_preset(preset_id)}
