"""Tests for TranscriptionResult — text accumulation and confidence calculation."""

from __future__ import annotations

import math

import pytest

from nexusvox.transcriber import TranscriptionResult


def test_initial_state():
    result = TranscriptionResult()
    assert result.text == ""
    assert result.done is False
    assert result.usage is None
    assert result.confidence is None
    assert result._logprobs == []


def test_append_accumulates_text():
    result = TranscriptionResult()
    result.append("Hello ")
    result.append("world")
    assert result.text == "Hello world"
    assert result.done is False


def test_append_collects_logprobs():
    result = TranscriptionResult()
    result.append("a", logprob=-0.5)
    result.append("b", logprob=-0.3)
    assert result._logprobs == [-0.5, -0.3]


def test_append_ignores_none_logprob():
    result = TranscriptionResult()
    result.append("a", logprob=None)
    result.append("b", logprob=-0.2)
    result.append("c")  # logprob defaults to None
    assert result._logprobs == [-0.2]


def test_finalize_sets_text_and_done():
    result = TranscriptionResult()
    result.append("partial ")
    result.append("text")
    result.finalize(text="Final text", usage={"prompt_tokens": 10, "completion_tokens": 5})

    assert result.text == "Final text"
    assert result.done is True
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_finalize_computes_confidence_from_logprobs():
    result = TranscriptionResult()
    result.append("a", logprob=-1.0)
    result.append("b", logprob=-2.0)
    result.finalize(text="ab")

    # avg_logprob = (-1.0 + -2.0) / 2 = -1.5
    # confidence = exp(-1.5) ≈ 0.22313
    assert result.confidence == pytest.approx(math.exp(-1.5), rel=1e-4)


def test_finalize_merges_additional_logprobs():
    result = TranscriptionResult()
    result.append("a", logprob=-1.0)
    result.finalize(text="abc", logprobs=[-2.0, -3.0])

    # Three logprobs: -1.0, -2.0, -3.0 → avg = -2.0
    assert result.confidence == pytest.approx(math.exp(-2.0), rel=1e-4)


def test_finalize_no_logprobs_confidence_none():
    result = TranscriptionResult()
    result.append("hello")
    result.append(" world")
    result.finalize(text="hello world")

    assert result.confidence is None


def test_finalize_empty_logprobs_list():
    result = TranscriptionResult()
    result.append("text")
    result.finalize(text="text", logprobs=[])

    assert result.confidence is None
