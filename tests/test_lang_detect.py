"""Tests for post-transcription language detection."""

from __future__ import annotations

import pytest

lingua = pytest.importorskip("lingua", reason="lingua-language-detector not installed")

from nexusvox.lang_detect import detect_language  # noqa: E402


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
