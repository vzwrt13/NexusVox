"""Tests for transcriber factory and OpenAIHttpTranscriber."""

from __future__ import annotations

import io
import wave

import pytest

from nexusvox.config import InferenceConfig
from nexusvox.transcriber import (
    BaseTranscriber,
    OpenAIHttpTranscriber,
    VoxtralRealtimeTranscriber,
    create_transcriber,
)


def test_factory_returns_openai_http_by_default():
    # The default model is Parakeet, which speaks the OpenAI-compatible HTTP protocol.
    config = InferenceConfig()
    transcriber = create_transcriber(config, device="cuda")
    assert isinstance(transcriber, OpenAIHttpTranscriber)


def test_factory_returns_voxtral_for_voxtral_model():
    # Voxtral is no longer the default, so the realtime WebSocket path needs its own case.
    config = InferenceConfig(server_url="ws://localhost:8000/v1/realtime", model="voxtral-mini-4b")
    transcriber = create_transcriber(config, device="cuda")
    assert isinstance(transcriber, VoxtralRealtimeTranscriber)


def test_factory_returns_openai_http_for_cohere_model():
    config = InferenceConfig(
        server_url="http://localhost:8001/v1/audio/transcriptions",
        model="cohere-transcribe",
    )
    transcriber = create_transcriber(config, device="cuda")
    assert isinstance(transcriber, OpenAIHttpTranscriber)


def test_factory_returns_openai_http_for_parakeet():
    config = InferenceConfig(
        server_url="http://localhost:8002/v1/audio/transcriptions",
        model="parakeet-tdt-0.6b",
    )
    transcriber = create_transcriber(config, device="cuda")
    assert isinstance(transcriber, OpenAIHttpTranscriber)


def test_factory_returns_openai_http_for_whisper_on_gpu():
    config = InferenceConfig(
        server_url="http://localhost:8003/v1/audio/transcriptions",
        model="whisper-large-v3-turbo",
    )
    transcriber = create_transcriber(config, device="cuda")
    assert isinstance(transcriber, OpenAIHttpTranscriber)


def test_factory_returns_local_whisper_on_cpu():
    from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber

    config = InferenceConfig(model="whisper-large-v3-turbo")
    transcriber = create_transcriber(config, device="cpu")
    assert isinstance(transcriber, LocalWhisperTranscriber)


def test_factory_returns_local_whisper_on_cpu_for_small():
    from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber

    config = InferenceConfig(model="whisper-small")
    transcriber = create_transcriber(config, device="cpu")
    assert isinstance(transcriber, LocalWhisperTranscriber)


@pytest.mark.parametrize("model", ["voxtral-mini-4b", "cohere-transcribe"])
def test_factory_raises_for_gpu_models_on_cpu(model):
    config = InferenceConfig(model=model)
    with pytest.raises(RuntimeError, match="requires a GPU"):
        create_transcriber(config, device="cpu")


def test_factory_raises_for_unknown_model():
    config = InferenceConfig(model="nonexistent-model")
    with pytest.raises(ValueError, match="Unknown model"):
        create_transcriber(config, device="cuda")


def test_all_transcribers_are_base_transcriber():
    voxtral = create_transcriber(InferenceConfig(model="voxtral-mini-4b"), device="cuda")
    cohere = create_transcriber(InferenceConfig(model="cohere-transcribe"), device="cuda")
    parakeet = create_transcriber(InferenceConfig(model="parakeet-tdt-0.6b"), device="cuda")
    whisper = create_transcriber(InferenceConfig(model="whisper-large-v3-turbo"), device="cuda")
    assert isinstance(voxtral, BaseTranscriber)
    assert isinstance(cohere, BaseTranscriber)
    assert isinstance(parakeet, BaseTranscriber)
    assert isinstance(whisper, BaseTranscriber)


def test_base_transcriber_needs_docker_default():
    assert BaseTranscriber.needs_docker is True


def test_voxtral_needs_docker():
    config = InferenceConfig(model="voxtral-mini-4b")
    assert create_transcriber(config, device="cuda").needs_docker is True


def test_local_whisper_does_not_need_docker():
    config = InferenceConfig(model="whisper-small")
    transcriber = create_transcriber(config, device="cpu")
    assert transcriber.needs_docker is False


@pytest.mark.asyncio
async def test_openai_http_buffers_audio_chunks():
    config = InferenceConfig(
        server_url="http://localhost:8001/v1/audio/transcriptions",
        model="cohere-transcribe",
    )
    transcriber = OpenAIHttpTranscriber(config)

    await transcriber.connect()
    assert transcriber.connected is True

    chunk1 = b"\x00\x01" * 100
    chunk2 = b"\x02\x03" * 100
    await transcriber.send_audio_chunk(chunk1)
    await transcriber.send_audio_chunk(chunk2)

    assert len(transcriber._audio_buffer) == 400


@pytest.mark.asyncio
async def test_openai_http_connect_resets_buffer():
    config = InferenceConfig(
        server_url="http://localhost:8001/v1/audio/transcriptions",
        model="cohere-transcribe",
    )
    transcriber = OpenAIHttpTranscriber(config)

    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00" * 100)
    assert len(transcriber._audio_buffer) == 100

    await transcriber.connect()
    assert len(transcriber._audio_buffer) == 0


@pytest.mark.asyncio
async def test_openai_http_disconnect_clears_state():
    config = InferenceConfig(
        server_url="http://localhost:8001/v1/audio/transcriptions",
        model="cohere-transcribe",
    )
    transcriber = OpenAIHttpTranscriber(config)

    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00" * 100)
    await transcriber.disconnect()

    assert transcriber.connected is False
    assert len(transcriber._audio_buffer) == 0


@pytest.mark.asyncio
async def test_openai_http_send_raises_when_not_connected():
    config = InferenceConfig(
        server_url="http://localhost:8001/v1/audio/transcriptions",
        model="cohere-transcribe",
    )
    transcriber = OpenAIHttpTranscriber(config)

    with pytest.raises(RuntimeError, match="Not connected"):
        await transcriber.send_audio_chunk(b"\x00" * 100)


def test_openai_http_model_name_cohere():
    config = InferenceConfig(model="cohere-transcribe")
    transcriber = OpenAIHttpTranscriber(config)
    assert transcriber.model == "CohereLabs/cohere-transcribe-03-2026"


def test_openai_http_model_name_parakeet():
    config = InferenceConfig(model="parakeet-tdt-0.6b")
    transcriber = OpenAIHttpTranscriber(config)
    assert transcriber.model == "nvidia/parakeet-tdt-0.6b-v3"


def test_openai_http_model_name_whisper():
    config = InferenceConfig(model="whisper-large-v3-turbo")
    transcriber = OpenAIHttpTranscriber(config)
    assert transcriber.model == "deepdml/faster-whisper-large-v3-turbo-ct2"


def test_voxtral_model_name():
    config = InferenceConfig()
    transcriber = VoxtralRealtimeTranscriber(config)
    assert transcriber.model == "mistralai/Voxtral-Mini-4B-Realtime-2602"


def test_wav_encoding_from_pcm16():
    """Verify that PCM16 bytes can be encoded as valid WAV (same logic as OpenAIHttpTranscriber.finish)."""
    sample_rate = 16_000
    pcm_data = b"\x00\x01\x02\x03" * 1000  # 4000 bytes = 2000 samples

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)

    wav_bytes = wav_buffer.getvalue()
    assert len(wav_bytes) > len(pcm_data)  # WAV header adds bytes

    # Verify it's a valid WAV
    wav_buffer.seek(0)
    with wave.open(wav_buffer, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == sample_rate
        assert wf.getnframes() == 2000


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_httpx(monkeypatch, payload):
    """Replace httpx.AsyncClient so a POST is captured instead of sent."""
    import httpx

    posted: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            posted.update(kwargs, url=url)
            return _FakeResponse(payload)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return posted


async def _run_cycle(transcriber):
    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00\x00" * 160)
    return await transcriber.finish()


@pytest.mark.asyncio
async def test_dictation_request_sends_only_file_and_model(monkeypatch):
    """Dictation never asks for words: the form is exactly what it was before word timestamps."""
    posted = _patch_httpx(monkeypatch, {"text": "hello"})

    result = await _run_cycle(OpenAIHttpTranscriber(InferenceConfig(model="parakeet-tdt-0.6b")))

    assert set(posted["files"]) == {"file"}
    assert posted["data"] == {"model": "nvidia/parakeet-tdt-0.6b-v3"}
    assert result.text == "hello"
    assert result.words == []


@pytest.mark.asyncio
async def test_word_timestamps_request_and_parse(monkeypatch):
    payload = {
        "text": "hello world",
        "words": [{"word": "hello", "start": 0.1, "end": 0.4}, {"word": "world", "start": 0.5, "end": 0.9}],
    }
    posted = _patch_httpx(monkeypatch, payload)
    transcriber = OpenAIHttpTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))
    transcriber.word_timestamps = True

    result = await _run_cycle(transcriber)

    assert posted["data"]["response_format"] == "verbose_json"
    assert posted["data"]["timestamp_granularities[]"] == "word"
    assert [(w.word, w.start, w.end) for w in result.words] == [("hello", 0.1, 0.4), ("world", 0.5, 0.9)]


@pytest.mark.asyncio
async def test_word_timestamps_requested_but_engine_returns_none(monkeypatch):
    """A server that ignores the extra fields (or sends junk) leaves words empty and the text intact."""
    _patch_httpx(monkeypatch, {"text": "hello", "words": [{"word": "x"}, "junk"]})
    transcriber = OpenAIHttpTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))
    transcriber.word_timestamps = True

    result = await _run_cycle(transcriber)

    assert result.text == "hello"
    assert result.words == []


@pytest.mark.asyncio
async def test_diarize_posts_the_whole_file_to_the_diarization_endpoint(monkeypatch):
    from nexusvox.transcriber import SpeakerTurn

    payload = {"segments": [{"speaker": 0, "start": 0.0, "end": 1.2}, {"speaker": 1, "start": 1.2, "end": 2.0}]}
    posted = _patch_httpx(monkeypatch, payload)
    transcriber = OpenAIHttpTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))

    turns = await transcriber.diarize(b"RIFF-wav", timeout=310.0)

    assert posted["url"] == "http://localhost:8002/v1/audio/diarization"
    assert posted["files"]["file"][1] == b"RIFF-wav"
    assert turns == [SpeakerTurn(0, 0.0, 1.2), SpeakerTurn(1, 1.2, 2.0)]


@pytest.mark.asyncio
async def test_engines_without_speaker_detection_refuse_to_diarize():
    from nexusvox.transcriber import BaseTranscriber, VoxtralRealtimeTranscriber

    assert BaseTranscriber.supports_diarization is False
    assert OpenAIHttpTranscriber.supports_diarization is True
    with pytest.raises(NotImplementedError):
        await VoxtralRealtimeTranscriber(InferenceConfig(model="voxtral-mini-4b")).diarize(b"", timeout=1.0)
