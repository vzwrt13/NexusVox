"""Tests for the optional LLM cleanup of uploaded files (chunking, labels, per-piece fallback)."""

from __future__ import annotations

import pytest

from nexusvox.config import LlmConfig
from nexusvox.file_cleanup import (
    MAX_CONSECUTIVE_FAILURES,
    _sentence_spans,
    _split,
    clean_transcript,
    split_paragraphs,
    split_pieces,
)
from nexusvox.llm import LlmError

LLM = LlmConfig(timeout_s=42.0)
IMPLAUSIBLE = "Paris is the capital of France and a very large city indeed."


def _fake_model(monkeypatch, reply):
    """Replace the model call; `reply(text)` gets the dictation inside the tags and returns the reply."""
    calls: list[tuple[float, str]] = []

    def complete(config, system, user):
        text = user.removeprefix("<dictation>").removesuffix("</dictation>")
        calls.append((config.timeout_s, text))
        return reply(text)

    monkeypatch.setattr("nexusvox.cleanup.complete", complete)
    return calls


def _sentences(n: int, words: int = 5) -> str:
    return " ".join(" ".join(f"w{i}x{j}" for j in range(words)) + "." for i in range(n))


# ---- chunking ----------------------------------------------------------------


def test_short_text_is_one_piece():
    assert split_pieces("Hello there. How are you?") == ["Hello there. How are you?"]


def test_long_text_is_cut_at_sentence_ends():
    pieces = split_pieces(_sentences(10), max_words=20)

    assert len(pieces) == 3  # 4 + 4 + 2 sentences of 5 words
    assert all(p.endswith(".") and len(p.split()) <= 20 for p in pieces)
    assert " ".join(pieces) == _sentences(10)


def test_a_sentence_longer_than_a_piece_is_cut_by_words():
    pieces = split_pieces(" ".join(f"w{i}" for i in range(25)), max_words=10)

    assert [len(p.split()) for p in pieces] == [10, 10, 5]


def test_german_text_is_chunked_like_english():
    text = "Das ist der erste Satz. Hier kommt der zweite Satz! Und wie geht es dir heute?"

    assert split_pieces(text, max_words=9) == [
        "Das ist der erste Satz.",
        "Hier kommt der zweite Satz!",
        "Und wie geht es dir heute?",
    ]


def test_speaker_blocks_become_paragraphs_with_their_label():
    paragraphs = split_paragraphs("Speaker 1: Hello there.\n\nSpeaker 2: Hi. How are you?")

    assert [(p.label, p.pieces) for p in paragraphs] == [
        ("Speaker 1: ", ["Hello there."]),
        ("Speaker 2: ", ["Hi. How are you?"]),
    ]


def test_a_long_speaker_block_is_split_into_pieces_under_one_label():
    paragraphs = split_paragraphs("Speaker 1: " + _sentences(10), max_words=20)

    assert len(paragraphs) == 1 and paragraphs[0].label == "Speaker 1: " and len(paragraphs[0].pieces) == 3


# ---- cleaning ----------------------------------------------------------------


def test_each_piece_is_cleaned_with_the_llm_timeout(monkeypatch):
    calls = _fake_model(monkeypatch, lambda text: text.replace("um ", ""))

    result = clean_transcript("Well um this is fine. And um that is fine too.", "light", LLM)

    assert result.text == "Well this is fine. And that is fine too."
    assert [timeout for timeout, _ in calls] == [LLM.timeout_s]  # not the 3 s dictation timeout
    assert result.failed == 0 and result.note == ""


def test_speaker_labels_stay_and_are_not_sent_to_the_model(monkeypatch):
    calls = _fake_model(monkeypatch, lambda text: text.replace("uh ", ""))

    result = clean_transcript(
        "Speaker 1: so uh this is the plan.\n\nSpeaker 2: yes uh that works for me.", "light", LLM
    )

    assert result.text == "Speaker 1: so this is the plan.\n\nSpeaker 2: yes that works for me."
    assert not any("Speaker" in text for _, text in calls)


def test_german_speaker_blocks_are_cleaned(monkeypatch):
    _fake_model(monkeypatch, lambda text: text.replace("äh ", ""))

    result = clean_transcript(
        "Speaker 1: das ist äh unser neuer Plan.\n\nSpeaker 2: ja äh das passt gut.", "light", LLM
    )

    assert result.text == "Speaker 1: das ist unser neuer Plan.\n\nSpeaker 2: ja das passt gut."


@pytest.mark.parametrize("failure", ["error", "implausible"])
def test_a_failed_piece_keeps_its_words_and_the_rest_is_cleaned(monkeypatch, failure):
    def reply(text):
        if text.startswith("Second"):
            if failure == "error":
                raise LlmError("timed out")
            return IMPLAUSIBLE
        return text.replace("um ", "")

    _fake_model(monkeypatch, reply)
    text = "Speaker 1: First um piece of the talk here.\n\nSpeaker 2: Second um piece of the talk here."

    result = clean_transcript(text, "light", LLM)

    assert result.text == "Speaker 1: First piece of the talk here.\n\nSpeaker 2: Second um piece of the talk here."
    assert (result.pieces, result.failed, result.skipped) == (2, 1, 0)
    assert "1 of 2" in result.note


def test_a_model_that_keeps_failing_is_given_up_on(monkeypatch):
    def reply(text):
        raise LlmError("connection refused")

    calls = _fake_model(monkeypatch, reply)
    text = "\n\n".join(f"Speaker 1: block {i} of the talk goes here." for i in range(10))

    result = clean_transcript(text, "medium", LLM)

    assert result.text == text
    assert len(calls) == MAX_CONSECUTIVE_FAILURES
    assert (result.pieces, result.failed, result.skipped) == (
        10,
        MAX_CONSECUTIVE_FAILURES,
        10 - MAX_CONSECUTIVE_FAILURES,
    )
    assert "10 of 10" in result.note and "kept failing" in result.note


def test_implausible_replies_do_not_trip_the_give_up_rule(monkeypatch):
    calls = _fake_model(monkeypatch, lambda text: IMPLAUSIBLE)
    text = "\n\n".join(f"Speaker 1: block {i} of the talk goes here." for i in range(6))

    result = clean_transcript(text, "light", LLM)

    assert len(calls) == 6 and result.skipped == 0 and result.failed == 6 and result.text == text


def test_progress_reports_each_piece(monkeypatch):
    _fake_model(monkeypatch, lambda text: text)
    seen = []

    clean_transcript("One piece of text here.\n\nAnother piece of text here.", "light", LLM, lambda *a: seen.append(a))

    assert seen == [("cleaning", 1, 2), ("cleaning", 2, 2)]


def test_an_unknown_level_or_empty_text_calls_no_model(monkeypatch):
    calls = _fake_model(monkeypatch, lambda text: text)

    assert clean_transcript("Some words in here.", "off", LLM).text == "Some words in here."
    assert clean_transcript("", "light", LLM).text == ""
    assert calls == []


# ---- text is kept as it was ----------------------------------------------------


def test_text_without_any_change_comes_back_untouched(monkeypatch):
    _fake_model(monkeypatch, lambda text: text)
    text = "Speaker 1:   Hello  there.\nSecond line.\n\n\nSpeaker 2: Fine.  \n"

    assert clean_transcript(text, "light", LLM).text == text


def test_a_dead_model_leaves_the_text_exactly_as_it_was(monkeypatch):
    def reply(text):
        raise LlmError("connection refused")

    _fake_model(monkeypatch, reply)
    text = "First sentence of the talk.\nSecond line of it.   Third one here."

    assert clean_transcript(text, "light", LLM).text == text


def test_whitespace_between_pieces_and_blocks_survives_a_change(monkeypatch):
    _fake_model(monkeypatch, lambda text: text.replace("um ", ""))
    text = "Speaker 1: Well um this is it.\nAnd more text.\n\n\nSpeaker 2: Fine by me."

    result = clean_transcript(text, "light", LLM)

    assert result.text == "Speaker 1: Well this is it.\nAnd more text.\n\n\nSpeaker 2: Fine by me."


def test_pieces_keep_the_whitespace_between_them():
    pieces, seps = _split("One two three.  Four five six.\nSeven eight nine.", 6)

    assert pieces == ["One two three.  Four five six.", "Seven eight nine."] and seps == ["\n"]


# ---- the give-up rule and short turns -------------------------------------------


def test_short_turns_between_failures_do_not_reset_the_give_up_rule(monkeypatch):
    def reply(text):
        raise LlmError("hung")

    calls = _fake_model(monkeypatch, reply)
    turns = []
    for i in range(10):
        turns += [f"Speaker 1: a long turn number {i} of the talk.", "Speaker 2: Yes please."]
    text = "\n\n".join(turns)

    result = clean_transcript(text, "light", LLM)

    assert len(calls) == MAX_CONSECUTIVE_FAILURES and result.text == text


def test_a_short_turn_with_a_filler_is_cleaned(monkeypatch):
    _fake_model(monkeypatch, lambda text: text.replace("um ", "").capitalize())

    result = clean_transcript("Speaker 1: Hello everyone here today.\n\nSpeaker 2: um hi.", "light", LLM)

    assert result.text == "Speaker 1: Hello everyone here today.\n\nSpeaker 2: Hi."


def test_a_one_word_turn_is_not_sent(monkeypatch):
    calls = _fake_model(monkeypatch, lambda text: text)

    clean_transcript("Speaker 2: Yes.", "light", LLM)

    assert calls == []


# ---- cutting without breaking sentences -----------------------------------------


def test_a_long_sentence_is_cut_after_a_comma_when_there_is_one():
    words = [f"w{i}" for i in range(30)]
    words[17] += ","
    text = " ".join(words) + "."

    pieces = split_pieces(text, max_words=20)

    assert len(pieces) == 2 and pieces[0].endswith("w17,") and " ".join(pieces) == text


@pytest.mark.parametrize(
    ("text", "second"),
    [
        ("Ask Dr. Smith about the plan today. It is ready.", "It is ready."),
        ("Bring things, z. B. paper and pens today. It is ready.", "It is ready."),
        ("Das gilt bzw. galt für alle Gäste heute. Es ist fertig.", "Es ist fertig."),
    ],
)
def test_a_sentence_does_not_end_at_an_abbreviation(text, second):
    spans = _sentence_spans(text)

    assert [text[a:b] for a, b in spans][1] == second and len(spans) == 2
