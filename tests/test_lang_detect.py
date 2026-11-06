"""Tests for post-transcription language detection."""

from __future__ import annotations

import pytest

lingua = pytest.importorskip("lingua", reason="lingua-language-detector not installed")

from nexusvox.dictation.lang_detect import detect_language  # noqa: E402


def test_detect_english():
    assert detect_language("This is a test of the English language.") == "en"


def test_detect_german():
    assert detect_language("Das ist ein Test der deutschen Sprache.") == "de"


def test_detect_unknown_empty():
    assert detect_language("") == "unknown"


def test_detect_unknown_short():
    """Very short text — lingua may or may not detect it. Document actual behavior."""
    result = detect_language("ok")
    assert result in ("en", "de", "unknown")


def test_detect_unsupported_language_maps_to_closest():
    """French is not in the detector — lingua picks the closest configured language."""
    result = detect_language("Ceci est un test complet de la langue francaise qui est assez long.")
    assert result in ("en", "de")


def test_stored_language_labels_and_undetermined():
    from nexusvox.dictation.lang_detect import UNDETERMINED, stored_language

    assert stored_language("This is a test of the English language.") == "en"
    assert stored_language("Das ist ein Test der deutschen Sprache.") == "de"
    assert stored_language("   ") == UNDETERMINED
    assert stored_language("") == UNDETERMINED


def test_stored_language_ignores_speaker_labels_and_long_text():
    from nexusvox.dictation.lang_detect import stored_language

    german = "Speaker 1: Das ist ein Test der deutschen Sprache, den wir heute machen.\n\n" * 3
    assert stored_language(german, strip_labels=True) == "de"
    assert stored_language("Das ist ein Test der deutschen Sprache. " * 5000, strip_labels=True) == "de"
