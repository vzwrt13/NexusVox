"""Post-transcription language detection using lingua."""

from __future__ import annotations

from lingua import IsoCode639_1, Language, LanguageDetectorBuilder

_detector = LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.GERMAN).build()

_LANG_MAP: dict[IsoCode639_1, str] = {
    IsoCode639_1.EN: "en",
    IsoCode639_1.DE: "de",
}


def language_guess(text: str) -> tuple[str, float]:
    """Most likely ISO 639-1 code for *text* and lingua's confidence in it (``("unknown", 0.0)`` if none)."""
    values = _detector.compute_language_confidence_values(text)
    if not values:
        return "unknown", 0.0
    return _LANG_MAP.get(values[0].language.iso_code_639_1, "unknown"), values[0].value


def detect_language(text: str) -> str:
    """Return ISO 639-1 code for *text*, or ``"unknown"``."""
    lang = _detector.detect_language_of(text)
    if lang is None:
        return "unknown"
    return _LANG_MAP.get(lang.iso_code_639_1, "unknown")
