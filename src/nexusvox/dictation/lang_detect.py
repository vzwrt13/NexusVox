"""Post-transcription language detection using lingua."""

from __future__ import annotations

import re

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


# What is stored for text whose language cannot be told (empty, or neither English nor German).
UNDETERMINED = "und"
# Enough text for a sure verdict; a longer transcript (a meeting) would only cost CPU time.
SAMPLE_CHARS = 2000
_SPEAKER_LABEL = re.compile(r"(?m)^[^\n:]{1,40}:[ \t]+")


def stored_language(text: str, *, strip_labels: bool = False) -> str:
    """The language label saved with a row: ``"en"``, ``"de"`` or ``UNDETERMINED``.

    `strip_labels` drops a leading ``Speaker N:`` style label from every line first (file transcripts
    and meetings carry them in English, whatever was spoken); only the first `SAMPLE_CHARS` are inspected.
    """
    if strip_labels:
        text = _SPEAKER_LABEL.sub("", text)
    sample = text[:SAMPLE_CHARS].strip()
    if not sample:
        return UNDETERMINED
    lang = detect_language(sample)
    return UNDETERMINED if lang == "unknown" else lang
