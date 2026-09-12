"""Tests for the correction dictionary: matching, suggestions, DB storage, and API."""

import json

import pytest

from nexusvox.dictionary import DictionaryEntry, apply_dictionary, suggest_entries

# ---- apply_dictionary -----------------------------------------------------


def test_apply_replaces_whole_words_case_insensitive():
    entries = [DictionaryEntry("nexus fox", "NexusVox")]
    result = apply_dictionary("I use Nexus Fox daily. nexus fox rocks.", entries)
    assert result == "I use NexusVox daily. NexusVox rocks."


def test_apply_does_not_touch_partial_words():
    entries = [DictionaryEntry("cat", "dog")]
    assert apply_dictionary("The cat sat on the catalog.", entries) == "The dog sat on the catalog."


def test_apply_longest_phrase_wins():
    entries = [DictionaryEntry("cloud code", "Claude Code"), DictionaryEntry("cloud code editor", "Claude Code IDE")]
    assert apply_dictionary("open cloud code editor and cloud code", entries) == "open Claude Code IDE and Claude Code"


def test_apply_tolerates_extra_whitespace_and_punctuation():
    entries = [DictionaryEntry("pull anfrage", "pull request")]
    assert apply_dictionary("Merge the pull  anfrage, please", entries) == "Merge the pull request, please"


def test_apply_empty_inputs():
    assert apply_dictionary("", [DictionaryEntry("a", "b")]) == ""
    assert apply_dictionary("text", []) == "text"
    assert apply_dictionary("text", [DictionaryEntry("   ", "b")]) == "text"


def test_apply_replacement_text_is_literal():
    entries = [DictionaryEntry("price", r"$5 \1")]
    assert apply_dictionary("the price", entries) == r"the $5 \1"


# ---- suggest_entries ------------------------------------------------------


def test_suggest_single_word_replacement():
    result = suggest_entries("Open cloud code now.", "Open Claude Code now.")
    assert result == [DictionaryEntry("cloud code", "Claude Code")]


def test_suggest_multiple_replacements():
    result = suggest_entries("nexus fox and cloud code", "NexusVox and Claude Code")
    assert result == [DictionaryEntry("nexus fox", "NexusVox"), DictionaryEntry("cloud code", "Claude Code")]


def test_suggest_ignores_insertions_and_deletions():
    assert suggest_entries("I um think so", "I think so") == []
    assert suggest_entries("I think so", "I really think so") == []


def test_suggest_skips_existing_entries():
    existing = [DictionaryEntry("cloud code", "Claude Code")]
    assert suggest_entries("open cloud code", "open Claude Code", existing) == []


def test_suggest_identical_texts():
    assert suggest_entries("same text", "same text") == []


def test_suggest_strips_trailing_punctuation():
    result = suggest_entries("Send it to nexus fox.", "Send it to NexusVox.")
    assert result == [DictionaryEntry("nexus fox", "NexusVox")]


# ---- Database -------------------------------------------------------------


def test_db_add_list_delete(db):
    entry = db.add_dictionary_entry("  nexus   fox ", " NexusVox ")
    assert entry == {"id": entry["id"], "wrong": "nexus fox", "right": "NexusVox"}
    assert db.list_dictionary() == [entry]
    assert db.get_dictionary_entries() == [DictionaryEntry("nexus fox", "NexusVox")]

    assert db.delete_dictionary_entry(entry["id"]) is True
    assert db.delete_dictionary_entry(entry["id"]) is False
    assert db.list_dictionary() == []


def test_db_add_updates_existing_case_insensitively(db):
    first = db.add_dictionary_entry("Cloud Code", "Claude Code")
    second = db.add_dictionary_entry("cloud code", "Claude Code CLI")
    assert second["id"] == first["id"]
    assert db.list_dictionary() == [{"id": first["id"], "wrong": "cloud code", "right": "Claude Code CLI"}]


@pytest.mark.parametrize("wrong,right", [("", "x"), ("x", ""), ("  ", "  ")])
def test_db_add_rejects_blank(db, wrong, right):
    assert db.add_dictionary_entry(wrong, right) is None
    assert db.list_dictionary() == []


# ---- Flask API ------------------------------------------------------------


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def test_dictionary_endpoints(flask_client):
    assert flask_client.get("/api/dictionary").get_json() == {"entries": []}

    res = _post(flask_client, "/api/dictionary", {"wrong": "nexus fox", "right": "NexusVox"}).get_json()
    assert res["ok"] is True
    entry_id = res["entry"]["id"]

    entries = flask_client.get("/api/dictionary").get_json()["entries"]
    assert entries == [{"id": entry_id, "wrong": "nexus fox", "right": "NexusVox"}]

    assert flask_client.delete(f"/api/dictionary/{entry_id}").get_json() == {"ok": True}
    assert flask_client.get("/api/dictionary").get_json() == {"entries": []}


def test_dictionary_endpoint_rejects_blank(flask_client):
    res = _post(flask_client, "/api/dictionary", {"wrong": "", "right": "x"}).get_json()
    assert res["ok"] is False


def test_review_correction_returns_suggestions(flask_client, db):
    record = db.save_transcription("open cloud code", "en", 1000, audio_path="audio/1.wav")

    res = _post(
        flask_client, f"/api/review/{record.id}", {"is_correct": False, "corrected_text": "open Claude Code"}
    ).get_json()

    assert res["ok"] is True
    assert res["suggestions"] == [{"wrong": "cloud code", "right": "Claude Code"}]


def test_review_correct_returns_no_suggestions(flask_client, db):
    record = db.save_transcription("fine", "en", 1000, audio_path="audio/1.wav")
    res = _post(flask_client, f"/api/review/{record.id}", {"is_correct": True}).get_json()
    assert res == {"ok": True, "suggestions": []}
