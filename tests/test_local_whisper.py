"""Tests for the in-process faster-whisper transcriber.

Mocks out `WhisperModel` via `LocalWhisperTranscriber._get_model` so these
tests don't require faster-whisper to be installed and don't download any
model weights.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from nexusvox.config import MODEL_REGISTRY, InferenceConfig
from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber


@dataclass
class _FakeSegment:
    text: str


class _FakeModel:
    def __init__(self, text: str = "hello world") -> None:
        self._text = text
        self.calls: list[dict] = []

    def transcribe(self, audio, beam_size=1, language=None):
        self.calls.append({"beam_size": beam_size, "language": language})
        return [_FakeSegment(text=self._text)], object()


def _reset_cache():
    LocalWhisperTranscriber._model_cache.clear()


@pytest.fixture(autouse=True)
def clear_cache():
    _reset_cache()
    yield
    _reset_cache()


def _make(device="cpu", compute_type="int8", model_id="whisper-small", **kwargs):
    return LocalWhisperTranscriber(
        InferenceConfig(model=model_id),
        device=device,
        compute_type=compute_type,
        **kwargs,
    )


def test_needs_docker_is_false():
    assert LocalWhisperTranscriber.needs_docker is False


def test_model_attribute_matches_registry_hf_name():
    t = _make(model_id="whisper-small")
    assert t.model == "Systran/faster-whisper-small"

    t2 = _make(model_id="whisper-large-v3-turbo")
    assert t2.model == "deepdml/faster-whisper-large-v3-turbo-ct2"


def test_auto_detect_language_nulls_language():
    t = _make(language="en", auto_detect_language=True)
    assert t._language is None

    t2 = _make(language="de", auto_detect_language=False)
    assert t2._language == "de"


@pytest.mark.asyncio
async def test_connect_resets_buffer_and_marks_connected(monkeypatch):
    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: _FakeModel()))
    t = _make()
    t._audio_buffer.extend(b"\x00\x01")
    await t.connect()
    assert t.connected is True
    assert len(t._audio_buffer) == 0


@pytest.mark.asyncio
async def test_send_audio_chunk_requires_connect(monkeypatch):
    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: _FakeModel()))
    t = _make()
    with pytest.raises(RuntimeError, match="Not connected"):
        await t.send_audio_chunk(b"\x00" * 10)


@pytest.mark.asyncio
async def test_finish_runs_model_and_returns_text(monkeypatch):
    fake = _FakeModel(text="transcribed text")
    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: fake))

    t = _make(language="en")
    await t.connect()
    await t.send_audio_chunk(b"\x00\x01" * 100)
    result = await t.finish(timeout=5.0)

    assert result.text == "transcribed text"
    assert result.done is True
    assert fake.calls and fake.calls[0]["language"] == "en"
    assert fake.calls[0]["beam_size"] == 1


@pytest.mark.asyncio
async def test_finish_joins_multi_segment_output(monkeypatch):
    class MultiSegmentModel:
        def transcribe(self, audio, beam_size=1, language=None):
            return [_FakeSegment(text="hello"), _FakeSegment(text="world")], object()

    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: MultiSegmentModel()))

    t = _make()
    await t.connect()
    await t.send_audio_chunk(b"\x00" * 32)
    result = await t.finish()

    assert result.text == "hello world"


@pytest.mark.asyncio
async def test_disconnect_clears_buffer_but_preserves_model_cache(monkeypatch):
    fake = _FakeModel()
    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: fake))

    t = _make()
    await t.connect()
    await t.send_audio_chunk(b"\x00" * 64)
    # prime the cache the way the real loader would
    LocalWhisperTranscriber._model_cache[(t._hf_name, t._device, t._compute_type)] = fake

    await t.disconnect()

    assert t.connected is False
    assert len(t._audio_buffer) == 0
    assert (t._hf_name, t._device, t._compute_type) in LocalWhisperTranscriber._model_cache


def test_model_cache_is_class_level_singleton():
    """Two instances with the same (hf_name, device, compute_type) share one model."""
    fake = _FakeModel()
    key = ("Systran/faster-whisper-small", "cpu", "int8")
    LocalWhisperTranscriber._model_cache[key] = fake

    t1 = _make()
    t2 = _make()
    got1 = LocalWhisperTranscriber._get_model(t1._hf_name, t1._device, t1._compute_type)
    got2 = LocalWhisperTranscriber._get_model(t2._hf_name, t2._device, t2._compute_type)

    assert got1 is fake
    assert got2 is fake
    assert got1 is got2


@pytest.mark.asyncio
async def test_finish_requires_connect(monkeypatch):
    monkeypatch.setattr(LocalWhisperTranscriber, "_get_model", classmethod(lambda cls, *a: _FakeModel()))
    t = _make()
    with pytest.raises(RuntimeError, match="Not connected"):
        await t.finish()


@pytest.mark.parametrize(
    "model_key",
    [k for k, v in MODEL_REGISTRY.items() if v.get("inprocess_supported")],
)
def test_inprocess_registry_entries_are_loadable(model_key):
    """Every inprocess_supported entry has a non-empty hf_name and constructs
    a LocalWhisperTranscriber without importing faster-whisper."""
    entry = MODEL_REGISTRY[model_key]
    assert entry["hf_name"], f"{model_key} has empty hf_name"
    assert entry["requires_gpu"] is False, f"{model_key} inprocess must have requires_gpu=False"

    t = _make(model_id=model_key)
    assert t.model == entry["hf_name"]
