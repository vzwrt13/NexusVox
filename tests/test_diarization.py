"""Tests for merging speaker turns with word timestamps (speaker diarization of file uploads)."""

from __future__ import annotations

from nexusvox.diarization import (
    TIMEOUT_BASE_S,
    diarization_timeout,
    format_transcript,
    label_words,
    speaker_at,
    speaker_paragraphs,
)
from nexusvox.transcriber import SpeakerTurn, Word, diarization_url, parse_turns


def _words(*spans: tuple[str, float, float]) -> list[Word]:
    return [Word(text, start, end) for text, start, end in spans]


def test_word_gets_the_speaker_whose_turn_holds_its_midpoint():
    turns = [SpeakerTurn(0, 0.0, 2.0), SpeakerTurn(1, 2.0, 4.0)]

    # Starts in speaker 0's turn but most of it (and the midpoint 2.1) is in speaker 1's.
    assert speaker_at(Word("across", 1.8, 2.4), turns) == 1
    assert speaker_at(Word("early", 0.2, 0.6), turns) == 0
    assert speaker_at(Word("late", 3.5, 3.9), turns) == 1


def test_overlapping_turns_go_to_the_one_covering_more_of_the_word():
    turns = [SpeakerTurn(0, 0.0, 4.0), SpeakerTurn(1, 2.0, 6.0)]  # both talk from 2 s to 4 s

    # Both midpoints (2.8 s, 4.0 s) lie in both turns.
    assert speaker_at(Word("mostly-first", 1.0, 4.6), turns) == 0
    assert speaker_at(Word("mostly-second", 2.5, 5.5), turns) == 1


def test_word_in_a_pause_goes_to_the_nearest_turn():
    turns = [SpeakerTurn(0, 0.0, 1.0), SpeakerTurn(1, 3.0, 4.0)]

    assert speaker_at(Word("trailing", 1.1, 1.3), turns) == 0
    assert speaker_at(Word("leading", 2.6, 2.9), turns) == 1
    assert speaker_at(Word("after", 4.5, 4.8), turns) == 1
    assert speaker_at(Word("before", -0.4, -0.2), turns) == 0


def test_no_turns_means_no_speaker():
    assert speaker_at(Word("alone", 0.0, 0.5), []) is None
    assert label_words(_words(("alone", 0.0, 0.5)), []) == [Word("alone", 0.0, 0.5)]


def test_speakers_are_numbered_in_order_of_first_appearance():
    # The model calls the first voice speaker_2; the transcript still starts with "Speaker 1".
    turns = [SpeakerTurn(2, 0.0, 1.0), SpeakerTurn(0, 1.0, 2.0), SpeakerTurn(2, 2.0, 3.0)]
    words = _words(("hi", 0.1, 0.4), ("hello", 1.2, 1.6), ("again", 2.2, 2.6))

    labelled = label_words(words, turns)

    assert [(w.word, w.speaker) for w in labelled] == [("hi", 1), ("hello", 2), ("again", 1)]
    assert [(w.start, w.end) for w in labelled] == [(w.start, w.end) for w in words]


def test_consecutive_words_of_one_speaker_form_a_paragraph():
    turns = [SpeakerTurn(0, 0.0, 2.0), SpeakerTurn(1, 2.0, 3.5), SpeakerTurn(0, 3.5, 6.0)]
    words = _words(
        ("Good", 0.1, 0.3),
        ("morning,", 0.4, 0.9),
        ("everyone.", 1.0, 1.6),
        ("Hi!", 2.2, 2.5),
        ("Guten", 2.6, 2.9),
        ("Morgen.", 3.0, 3.4),
        ("Let's", 3.7, 3.9),
        ("start.", 4.0, 4.4),
    )

    labelled = label_words(words, turns)

    assert speaker_paragraphs(labelled) == [
        (1, "Good morning, everyone."),
        (2, "Hi! Guten Morgen."),
        (1, "Let's start."),
    ]
    assert format_transcript(labelled) == (
        "Speaker 1: Good morning, everyone.\n\nSpeaker 2: Hi! Guten Morgen.\n\nSpeaker 1: Let's start."
    )


def test_four_speakers_across_a_long_file():
    turns = [SpeakerTurn(i % 4, i * 30.0, (i + 1) * 30.0) for i in range(8)]  # 4 minutes, turns of 30 s
    words = [Word(f"w{i}", i * 30.0 + 10.0, i * 30.0 + 10.5) for i in range(8)]

    paragraphs = speaker_paragraphs(label_words(words, turns))

    assert [speaker for speaker, _ in paragraphs] == [1, 2, 3, 4, 1, 2, 3, 4]


def test_timeout_scales_with_file_length():
    assert diarization_timeout(0) == TIMEOUT_BASE_S
    assert diarization_timeout(-5) == TIMEOUT_BASE_S
    assert diarization_timeout(1800) > diarization_timeout(60) > TIMEOUT_BASE_S


def test_parse_turns_skips_malformed_entries():
    raw = [
        {"speaker": 0, "start": 0.0, "end": 1.5},
        {"speaker": "1", "start": "1.5", "end": "3"},
        {"speaker": 2, "start": 4.0},  # no end
        {"speaker": "x", "start": 0, "end": 1},
        {"speaker": 3, "start": 5.0, "end": 4.0},  # ends before it starts
        "junk",
    ]

    assert parse_turns(raw) == [SpeakerTurn(0, 0.0, 1.5), SpeakerTurn(1, 1.5, 3.0)]
    assert parse_turns(None) == []
    assert parse_turns({"speaker": 0}) == []


def test_diarization_url_sits_next_to_the_transcription_endpoint():
    assert (
        diarization_url("http://localhost:8002/v1/audio/transcriptions") == "http://localhost:8002/v1/audio/diarization"
    )
    assert diarization_url("http://gpu-box:8002/v1/audio/transcriptions/") == "http://gpu-box:8002/v1/audio/diarization"
    # Without the transcription path: the container's own route on the same host.
    assert diarization_url("http://gpu-box:8002") == "http://gpu-box:8002/v1/audio/diarization"
    assert diarization_url("http://localhost:9000/asr") == "http://localhost:9000/v1/audio/diarization"


def test_speaker_detection_models_come_from_the_registry(monkeypatch):
    from nexusvox import diarization

    assert diarization.speaker_detection_models() == ["Parakeet TDT 0.6B"]
    monkeypatch.setitem(diarization.MODEL_REGISTRY, "other", {"display_name": "Other", "diarization": True})
    assert diarization.speaker_detection_models() == ["Parakeet TDT 0.6B", "Other"]
