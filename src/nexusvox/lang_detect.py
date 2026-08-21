"""Post-transcription language detection using lingua."""

from __future__ import annotations

from lingua import IsoCode639_1, Language, LanguageDetectorBuilder

_detector = LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.GERMAN).build()

_LANG_MAP: dict[IsoCode639_1, str] = {
    IsoCode639_1.EN: "en",
    IsoCode639_1.DE: "de",
}


def detect_language(text: str) -> str:
    """Return ISO 639-1 code for *text*, or ``"unknown"``."""
    lang = _detector.detect_language_of(text)
    if lang is None:
        return "unknown"
    return _LANG_MAP.get(lang.iso_code_639_1, "unknown")
