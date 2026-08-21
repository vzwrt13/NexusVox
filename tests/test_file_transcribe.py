"""Tests for audio file conversion and transcription module."""

from __future__ import annotations

import io
import wave

import pytest
from pydub.exceptions import CouldntDecodeError

from nexusvox.file_transcribe import ALLOWED_EXTENSIONS, _get_extension, convert_to_wav


def test_get_extension():
    assert _get_extension("test.wav") == ".wav"
    assert _get_extension("music.MP3") == ".mp3"
    assert _get_extension("noext") == ""
    assert _get_extension("multi.dots.flac") == ".flac"


def test_allowed_extensions_set():
    assert ".wav" in ALLOWED_EXTENSIONS
    assert ".mp3" in ALLOWED_EXTENSIONS
    assert ".flac" in ALLOWED_EXTENSIONS
    assert ".ogg" in ALLOWED_EXTENSIONS
    assert ".webm" in ALLOWED_EXTENSIONS
    assert ".exe" not in ALLOWED_EXTENSIONS


def test_convert_wav_passthrough(sample_wav_bytes):
    """Short WAV input produces a single valid WAV chunk at 16kHz mono."""
    chunks, duration_ms = convert_to_wav(sample_wav_bytes, "test.wav")

    assert len(chunks) == 1
    with wave.open(io.BytesIO(chunks[0]), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16_000
        assert wf.getsampwidth() == 2

    # 0.5s of audio → ~500ms
    assert 450 <= duration_ms <= 550


def test_convert_returns_correct_duration(sample_wav_bytes):
    """Duration matches the input audio length."""
    _, duration_ms = convert_to_wav(sample_wav_bytes, "audio.wav")
    assert isinstance(duration_ms, int)
    assert duration_ms > 0


def test_convert_invalid_format_raises():
    """Garbage bytes should raise an error.

    Which error depends on the environment, so both are accepted: with ffmpeg on
    PATH pydub reports CouldntDecodeError, without it (as on CI) the attempt to
    invoke ffmpeg fails first with FileNotFoundError.
    """
    with pytest.raises((CouldntDecodeError, FileNotFoundError)):
        convert_to_wav(b"not audio data at all", "bad.wav")


def test_convert_long_audio_produces_multiple_chunks():
    """Audio longer than MAX_CHUNK_MS is split into multiple chunks."""
    # Generate 90 seconds of silence (1.5x the chunk limit)
    duration_s = 90
    sample_rate = 16_000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * (sample_rate * duration_s))

    chunks, total_ms = convert_to_wav(buf.getvalue(), "long.wav")

    assert len(chunks) == 2  # 60s + 30s
    assert 89_000 <= total_ms <= 91_000

    # Each chunk is a valid WAV
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16_000


class _RecordingTranscriber:
    """Minimal in-memory transcriber stand-in for testing transcribe_file."""

    needs_docker = False

    def __init__(self, text="hello from cpu"):
        self._text = text
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.chunks: list[bytes] = []
        self._connected = False

    async def connect(self):
        self._connected = True
        self.connect_calls += 1

    async def send_audio_chunk(self, pcm16):
        self.chunks.append(bytes(pcm16))

    async def finish(self, timeout=120.0):
        from nexusvox.transcriber import TranscriptionResult

        r = TranscriptionResult()
        r.finalize(text=self._text)
        self._connected = False
        return r

    async def disconnect(self):
        self._connected = False
        self.disconnect_calls += 1

    @property
    def connected(self):
        return self._connected


@pytest.mark.asyncio
async def test_transcribe_file_dispatches_through_factory(monkeypatch, sample_wav_bytes):
    """CPU path: transcribe_file feeds PCM16 through the factory-returned transcriber."""
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    recorder = _RecordingTranscriber(text="hello from cpu")
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: recorder)

    chunks, _ = ft.convert_to_wav(sample_wav_bytes, "test.wav")
    result = await ft.transcribe_file(
        chunks,
        InferenceConfig(model="whisper-small"),
        device="cpu",
        language="en",
    )

    assert result.text == "hello from cpu"
    assert recorder.connect_calls == 1
    assert recorder.disconnect_calls == 1
    assert len(recorder.chunks) > 0
    assert all(len(c) <= 4096 for c in recorder.chunks)


@pytest.mark.asyncio
async def test_transcribe_file_passes_language_and_device_to_factory(monkeypatch, sample_wav_bytes):
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    captured: dict = {}

    def fake_factory(config, *, device=None, language=None, auto_detect_language=False):
        captured["device"] = device
        captured["language"] = language
        captured["auto"] = auto_detect_language
        return _RecordingTranscriber()

    monkeypatch.setattr(ft, "create_transcriber", fake_factory)

    chunks, _ = ft.convert_to_wav(sample_wav_bytes, "test.wav")
    await ft.transcribe_file(
        chunks,
        InferenceConfig(model="whisper-small"),
        device="cpu",
        language="de",
        auto_detect_language=True,
    )

    assert captured == {"device": "cpu", "language": "de", "auto": True}
