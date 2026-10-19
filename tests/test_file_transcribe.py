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


class _ScriptedTranscriber(_RecordingTranscriber):
    """Returns one scripted (text, words, done) per chunk, in order."""

    supports_word_timestamps = True

    def __init__(self, script):
        super().__init__()
        self._script = list(script)
        self.word_timestamps = False

    async def finish(self, timeout=120.0):
        from nexusvox.transcriber import TranscriptionResult

        text, words, done = self._script.pop(0)
        r = TranscriptionResult()
        if done:
            r.finalize(text=text)
        r.words = words
        return r


def _silent_wav(seconds=1.0):
    from nexusvox.transcriber import pcm16_to_wav_bytes

    return pcm16_to_wav_bytes(bytes(2) * int(16_000 * seconds), sample_rate=16_000)


@pytest.mark.asyncio
async def test_word_times_are_shifted_by_the_audio_before_each_chunk(monkeypatch):
    """The offset is the real length of the earlier chunks, whatever size the chunks are."""
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcriber import Word

    script = [
        ("one two", [Word("one", 0.5, 0.9), Word("two", 0.95, 1.0)], True),
        ("three", [Word("three", 0.25, 0.5)], True),
        ("four", [Word("four", 0.0, 0.1)], True),
    ]
    transcriber = _ScriptedTranscriber(script)
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav(1.0), _silent_wav(0.5), _silent_wav(0.5)],
        InferenceConfig(model="parakeet-tdt-0.6b"),
        word_timestamps=True,
    )

    assert transcriber.word_timestamps is True
    assert result.text == "one two three four"
    assert [(w.word, w.start, w.end) for w in result.words] == [
        ("one", 0.5, 0.9),
        ("two", 0.95, 1.0),
        ("three", 1.25, 1.5),
        ("four", 1.5, 1.6),
    ]
    assert result.notes == []


@pytest.mark.asyncio
async def test_engine_without_timestamps_leaves_words_empty(monkeypatch):
    """Whisper and the others never get the flag, return no words, and that is not a problem."""
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("hello", [], True), ("again", [], True)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav(), _silent_wav()], InferenceConfig(model="whisper-small"), device="cpu", word_timestamps=True
    )

    assert transcriber.word_timestamps is False
    assert result.text == "hello again"
    assert result.words == []
    assert result.notes == []


@pytest.mark.asyncio
async def test_words_are_not_requested_unless_the_caller_asks(monkeypatch):
    """Review-tab retry calls transcribe_file without the flag: no extra server work, no word notes."""
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("hello", [], True)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"))

    assert transcriber.word_timestamps is False
    assert result.notes == []


@pytest.mark.asyncio
async def test_client_that_cannot_report_words_is_not_asked(monkeypatch):
    """A registry flag on a model whose client has no word support must not set the flag or add notes."""
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("hello", [], True)])
    transcriber.supports_word_timestamps = False
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), word_timestamps=True)

    assert transcriber.word_timestamps is False
    assert result.notes == []


@pytest.mark.asyncio
async def test_timed_out_or_wordless_chunk_is_reported(monkeypatch, caplog):
    import logging

    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcriber import Word

    script = [
        ("", [], False),  # timed out
        ("spoken", [], True),  # text but no words
        ("fine", [Word("fine", 0.1, 0.2)], True),
    ]
    transcriber = _ScriptedTranscriber(script)
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    with caplog.at_level(logging.WARNING, logger=ft.logger.name):
        result = await ft.transcribe_file(
            [_silent_wav(), _silent_wav(), _silent_wav()],
            InferenceConfig(model="parakeet-tdt-0.6b"),
            word_timestamps=True,
        )

    assert result.text == "spoken fine"
    assert len(result.notes) == 2
    assert "Chunk 1 of 3 timed out" in result.notes[0]
    assert "Chunk 2 of 3" in result.notes[1] and "word timestamps" in result.notes[1]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == result.notes


@pytest.mark.asyncio
async def test_timeout_note_does_not_mention_words_for_engines_without_them(monkeypatch):
    import nexusvox.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("", [], False)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav()], InferenceConfig(model="whisper-small"), device="cpu", word_timestamps=True
    )

    assert len(result.notes) == 1
    assert "timed out" in result.notes[0]
    assert "word" not in result.notes[0]
