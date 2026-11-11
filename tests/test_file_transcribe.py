"""Tests for audio file conversion and transcription module."""

from __future__ import annotations

import io
import tempfile
import tracemalloc
import wave
from pathlib import Path

import pytest

from nexusvox.transcription.file_transcribe import ALLOWED_EXTENSIONS, convert_to_wav
from nexusvox.transcription.transcriber import Word


def write_wav_file(path: Path, seconds: float, *, rate: int = 16_000, channels: int = 1, width: int = 2) -> Path:
    """A silent WAV on disk, written a second at a time (so a long one never sits in memory)."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        for _ in range(int(seconds)):
            wf.writeframes(bytes(width * channels * rate))
        wf.writeframes(bytes(width * channels * int(rate * (seconds % 1))))
    return path


def test_allowed_extensions_set():
    assert ".wav" in ALLOWED_EXTENSIONS
    assert ".mp3" in ALLOWED_EXTENSIONS
    assert ".flac" in ALLOWED_EXTENSIONS
    assert ".ogg" in ALLOWED_EXTENSIONS
    assert ".webm" in ALLOWED_EXTENSIONS
    assert ".exe" not in ALLOWED_EXTENSIONS


def test_convert_wav_passthrough(tmp_path, sample_wav_bytes):
    """Short WAV input produces a single valid WAV chunk at 16kHz mono."""
    source = tmp_path / "test.wav"
    source.write_bytes(sample_wav_bytes)

    with convert_to_wav(source) as audio:
        chunks = list(audio)
        duration_ms = audio.duration_ms

    assert len(chunks) == 1
    with wave.open(io.BytesIO(chunks[0]), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16_000
        assert wf.getsampwidth() == 2

    # 0.5s of audio → ~500ms
    assert 450 <= duration_ms <= 550


def test_convert_returns_correct_duration(tmp_path, sample_wav_bytes):
    """Duration matches the input audio length."""
    source = tmp_path / "audio.wav"
    source.write_bytes(sample_wav_bytes)

    with convert_to_wav(source) as audio:
        assert isinstance(audio.duration_ms, int)
        assert audio.duration_ms > 0


def test_convert_resamples_and_downmixes_a_stereo_wav(tmp_path):
    source = write_wav_file(tmp_path / "stereo.wav", 2, rate=44_100, channels=2)

    with convert_to_wav(source) as audio:
        with wave.open(io.BytesIO(audio[0]), "rb") as wf:
            assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (1, 16_000, 2)
            assert wf.getnframes() == 32_000
        assert audio.duration_ms == 2000


def test_convert_invalid_format_raises(tmp_path):
    """Garbage bytes raise a ValueError and leave no temporary file behind."""
    source = tmp_path / "bad.wav"
    source.write_bytes(b"not audio data at all")
    before = set(Path(tempfile.gettempdir()).glob("nexusvox-convert-*"))

    with pytest.raises(ValueError, match="could not read the file as audio"):
        convert_to_wav(source)

    assert set(Path(tempfile.gettempdir()).glob("nexusvox-convert-*")) == before


@pytest.mark.parametrize("width", [1, 3, 4])
def test_a_wav_with_another_sample_size_is_converted_in_process(tmp_path, width):
    """8-, 24- and 32-bit PCM WAVs (a DAW's output) need no ffmpeg on PATH: PyAV reads them."""
    source = write_wav_file(tmp_path / "deep.wav", 1, rate=48_000, channels=2, width=width)

    with convert_to_wav(source) as audio:
        assert abs(audio.duration_ms - 1000) <= 50
        with wave.open(io.BytesIO(audio[0]), "rb") as wf:
            assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (1, 16_000, 2)


def _write_flac(path: Path, seconds: int, rate: int = 44_100) -> Path:
    import av
    import numpy as np

    with av.open(str(path), "w") as container:
        stream = container.add_stream("flac", rate=rate)
        stream.layout = "stereo"
        for _ in range(seconds):
            tone = (np.sin(np.linspace(0, 2 * np.pi * 440 * rate / rate, rate)) * 8000).astype(np.int16)
            frame = av.AudioFrame.from_ndarray(np.stack([tone, tone]).T.reshape(1, -1), format="s16", layout="stereo")
            frame.sample_rate = rate
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return path


def test_a_compressed_format_is_decoded_in_process(tmp_path):
    """FLAC needs no ffmpeg on PATH; the name's extension does not pick the decoder."""
    source = _write_flac(tmp_path / "talk.flac", 3)
    renamed = source.rename(tmp_path / "talk.upload")

    with convert_to_wav(renamed) as audio:
        assert abs(audio.duration_ms - 3000) <= 50
        with wave.open(io.BytesIO(audio[0]), "rb") as wf:
            samples = wf.readframes(wf.getnframes())
        assert any(samples)  # real audio came through, not silence


def test_a_file_without_audio_is_refused(tmp_path):
    import av

    source = tmp_path / "video.mp4"
    with av.open(str(source), "w") as container:
        container.add_stream("mpeg4", rate=10)  # a video stream and no audio

    with pytest.raises(ValueError):
        convert_to_wav(source)


def test_convert_long_audio_produces_multiple_chunks(tmp_path):
    """Audio longer than MAX_CHUNK_MS is split into multiple chunks."""
    source = write_wav_file(tmp_path / "long.wav", 90)  # 1.5x the chunk limit

    with convert_to_wav(source) as audio:
        assert len(audio) == 2  # 60s + 30s
        assert 89_000 <= audio.duration_ms <= 91_000
        chunks = list(audio)
        assert list(audio) == chunks  # repeatable: speaker detection reads the chunks a second time
        assert audio[-1] == chunks[1]

    # Each chunk is a valid WAV
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16_000
    with pytest.raises(IndexError):
        audio[2]


def test_a_converted_file_that_cannot_be_deleted_does_not_fail_the_job(tmp_path, monkeypatch):
    """A scanner holding the file on Windows must not throw a finished transcription away."""
    source = write_wav_file(tmp_path / "a.wav", 1)
    audio = convert_to_wav(source)

    def held(self, missing_ok=False):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(Path, "unlink", held)
    audio.close()  # logs, does not raise
    monkeypatch.undo()
    audio.close()


def test_closing_deletes_the_converted_file(tmp_path):
    source = write_wav_file(tmp_path / "a.wav", 1)

    audio = convert_to_wav(source)
    converted = audio.path
    assert converted.exists()
    audio.close()

    assert not converted.exists()
    assert source.exists()  # the caller's own file is never touched


def _peak_memory_of_converting_and_reading(source: Path) -> tuple[int, int]:
    """(peak traced bytes, chunks) of converting `source` and walking every chunk of the result."""
    tracemalloc.start()
    try:
        with convert_to_wav(source) as audio:
            for _chunk in audio:
                pass
            return tracemalloc.get_traced_memory()[1], len(audio)
    finally:
        tracemalloc.stop()


def test_peak_memory_does_not_grow_with_the_length_of_the_file(tmp_path):
    """Ten times the audio costs no more memory: the file is converted and read one 60 s window at a time."""
    short = write_wav_file(tmp_path / "short.wav", 2 * 60, rate=24_000)
    long = write_wav_file(tmp_path / "long.wav", 20 * 60, rate=24_000)  # 57 MB on disk

    short_peak, short_chunks = _peak_memory_of_converting_and_reading(short)
    long_peak, long_chunks = _peak_memory_of_converting_and_reading(long)

    assert (short_chunks, long_chunks) == (2, 20)
    assert long_peak < short_peak * 1.5


@pytest.mark.asyncio
async def test_a_large_file_is_transcribed_through_the_chunk_path_and_cleaned_up(tmp_path, monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    recorder = _RecordingTranscriber(text="ok")
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: recorder)
    source = write_wav_file(tmp_path / "big.wav", 30 * 60, rate=16_000)  # 30 minutes: 30 chunks

    with convert_to_wav(source) as audio:
        converted = audio.path
        result = await ft.transcribe_file(audio, InferenceConfig(model="whisper-large-v3-turbo"), device="cpu")

    assert result.text == " ".join(["ok"] * 30)
    assert recorder.connect_calls == 30
    assert sum(len(c) for c in recorder.chunks) == 30 * 60 * 16_000 * 2
    assert not converted.exists()


class _RecordingTranscriber:
    """Minimal in-memory transcriber stand-in for testing transcribe_file."""

    needs_docker = False

    async def prepare(self):
        pass

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
        from nexusvox.transcription.transcriber import TranscriptionResult

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
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    recorder = _RecordingTranscriber(text="hello from cpu")
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: recorder)

    result = await ft.transcribe_file(
        [sample_wav_bytes],
        InferenceConfig(model="whisper-large-v3-turbo"),
        device="cpu",
    )

    assert result.text == "hello from cpu"
    assert recorder.connect_calls == 1
    assert recorder.disconnect_calls == 1
    assert len(recorder.chunks) > 0
    assert all(len(c) <= 4096 for c in recorder.chunks)


@pytest.mark.asyncio
async def test_transcribe_file_passes_device_to_factory(monkeypatch, sample_wav_bytes):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    captured: dict = {}

    def fake_factory(config, *, device=None, auto_fallback=False):
        captured["device"] = device
        return _RecordingTranscriber()

    monkeypatch.setattr(ft, "create_transcriber", fake_factory)

    await ft.transcribe_file(
        [sample_wav_bytes],
        InferenceConfig(model="whisper-large-v3-turbo"),
        device="cpu",
    )

    assert captured == {"device": "cpu"}


class _ScriptedTranscriber(_RecordingTranscriber):
    """Returns one scripted (text, words, done) per chunk, in order."""

    supports_word_timestamps = True

    def __init__(self, script):
        super().__init__()
        self._script = list(script)
        self.word_timestamps = False

    async def finish(self, timeout=120.0):
        from nexusvox.transcription.transcriber import TranscriptionResult

        text, words, done = self._script.pop(0)
        r = TranscriptionResult()
        if done:
            r.finalize(text=text)
        r.words = words
        return r


def _silent_wav(seconds=1.0):
    from nexusvox.transcription.transcriber import pcm16_to_wav_bytes

    return pcm16_to_wav_bytes(bytes(2) * int(16_000 * seconds), sample_rate=16_000)


@pytest.mark.asyncio
async def test_word_times_are_shifted_by_the_audio_before_each_chunk(monkeypatch):
    """The offset is the real length of the earlier chunks, whatever size the chunks are."""
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import Word

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
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("hello", [], True), ("again", [], True)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav(), _silent_wav()],
        InferenceConfig(model="whisper-large-v3-turbo"),
        device="cpu",
        word_timestamps=True,
    )

    assert transcriber.word_timestamps is False
    assert result.text == "hello again"
    assert result.words == []
    assert result.notes == []


@pytest.mark.asyncio
async def test_words_are_not_requested_unless_the_caller_asks(monkeypatch):
    """Review-tab retry calls transcribe_file without the flag: no extra server work, no word notes."""
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("hello", [], True)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"))

    assert transcriber.word_timestamps is False
    assert result.notes == []


@pytest.mark.asyncio
async def test_client_that_cannot_report_words_is_not_asked(monkeypatch):
    """A registry flag on a model whose client has no word support must not set the flag or add notes."""
    import nexusvox.transcription.file_transcribe as ft
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

    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import Word

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
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("", [], False)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav()], InferenceConfig(model="whisper-large-v3-turbo"), device="cpu", word_timestamps=True
    )

    assert len(result.notes) == 1
    assert "timed out" in result.notes[0]
    assert "word" not in result.notes[0]


class _DiarizingTranscriber(_ScriptedTranscriber):
    """Scripted chunks plus a scripted speaker-detection reply (turns, or an exception to raise)."""

    supports_diarization = True

    def __init__(self, script, turns):
        super().__init__(script)
        self._turns = turns
        self.diarize_calls: list[tuple[bytes, float]] = []

    async def diarize(self, wav, timeout):
        self.diarize_calls.append((wav if isinstance(wav, bytes) else wav.read(), timeout))
        if isinstance(self._turns, Exception):
            raise self._turns
        return self._turns


def _wav_seconds(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


@pytest.mark.asyncio
async def test_diarize_labels_the_transcript_with_one_call_over_the_whole_file(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.diarization import diarization_timeout
    from nexusvox.transcription.transcriber import SpeakerTurn, Word

    script = [
        ("Hello there. Hi!", [Word("Hello", 0.1, 0.3), Word("there.", 0.35, 0.6), Word("Hi!", 0.8, 0.95)], True),
        ("How are you?", [Word("How", 0.1, 0.2), Word("are", 0.25, 0.3), Word("you?", 0.35, 0.45)], True),
    ]
    # Speaker 3's turn spans the chunk border (1.0 s): the second chunk continues it.
    turns = [SpeakerTurn(0, 0.0, 0.7), SpeakerTurn(3, 0.7, 1.5)]
    transcriber = _DiarizingTranscriber(script, turns)
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav(1.0), _silent_wav(0.5)], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True
    )

    assert transcriber.word_timestamps is True  # diarize implies words
    assert len(transcriber.diarize_calls) == 1
    wav_bytes, timeout = transcriber.diarize_calls[0]
    assert _wav_seconds(wav_bytes) == 1.5
    assert timeout == diarization_timeout(1.5)
    assert result.text == "Speaker 1: Hello there.\n\nSpeaker 2: Hi! How are you?"
    assert [w.speaker for w in result.words] == [1, 1, 2, 2, 2, 2]
    assert result.words[3].start == 1.1  # still shifted to the file's timeline
    assert result.notes == []


@pytest.mark.asyncio
async def test_without_diarize_the_server_is_not_asked_for_speakers(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn, Word

    transcriber = _DiarizingTranscriber([("hi", [Word("hi", 0.1, 0.2)], True)], [SpeakerTurn(0, 0.0, 1.0)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), word_timestamps=True)

    assert transcriber.diarize_calls == []
    assert result.text == "hi"
    assert result.words[0].speaker is None


@pytest.mark.asyncio
async def test_a_recording_over_the_detection_cap_skips_speaker_detection_with_a_note(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import Word

    transcriber = _DiarizingTranscriber([("hi", [Word("hi", 0.1, 0.2)], True)], [])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)
    monkeypatch.setattr(ft, "MAX_DETECTION_S", 0.5)

    result = await ft.transcribe_file([_silent_wav(1.0)], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)

    assert transcriber.diarize_calls == []  # no request was sent
    assert result.text == "hi"
    assert len(result.notes) == 1
    assert "Speaker detection skipped" in result.notes[0] and "longer than" in result.notes[0]
    assert result.notes[0].endswith(ft.NO_SPEAKER_LABELS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_name", "expected"),
    [("ReadTimeout", "timed out after"), ("ConnectError", "connection refused")],
)
async def test_failed_speaker_detection_keeps_the_plain_transcript_with_a_note(monkeypatch, error_name, expected):
    import httpx

    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import Word

    error = getattr(httpx, error_name)("connection refused")
    script = [("hi there", [Word("hi", 0.1, 0.2), Word("there", 0.3, 0.5)], True)]
    transcriber = _DiarizingTranscriber(script, error)
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)

    assert result.text == "hi there"
    assert all(w.speaker is None for w in result.words)
    assert len(result.notes) == 1
    assert "Speaker detection failed" in result.notes[0] and expected in result.notes[0]
    assert transcriber.disconnect_calls == 1


@pytest.mark.asyncio
async def test_no_turns_found_keeps_the_plain_transcript_with_a_note(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import Word

    transcriber = _DiarizingTranscriber([("hi", [Word("hi", 0.1, 0.2)], True)], [])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)

    assert result.text == "hi"
    assert result.notes == ["Speaker detection found no speakers; the transcript has no speaker labels."]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "script",
    [
        # a chunk without any words
        [("first", [Word("first", 0.1, 0.4)], True), ("second", [], True)],
        # a chunk where the server dropped one word's timing: 3 words of text, 2 timed
        [("one two three", [Word("one", 0.1, 0.2), Word("three", 0.5, 0.6)], True)],
    ],
)
async def test_speaker_detection_is_skipped_when_a_word_has_no_timing(monkeypatch, script):
    """Labelled paragraphs come from the words, so an untimed word would vanish: keep the full plain text."""
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn

    transcriber = _DiarizingTranscriber(script, [SpeakerTurn(0, 0.0, 2.0)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav() for _ in script], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True
    )

    assert result.text == " ".join(text for text, _, _ in script)
    assert all(w.speaker is None for w in result.words)
    assert result.notes[-1] == "Speaker detection skipped: some words came back without timing."


@pytest.mark.asyncio
async def test_a_timed_out_chunk_does_not_block_speaker_labels(monkeypatch):
    """Nothing of a timed-out chunk is in the plain text either, so the labelled text loses nothing."""
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn

    script = [("", [], False), ("hello", [Word("hello", 0.1, 0.4)], True)]
    transcriber = _DiarizingTranscriber(script, [SpeakerTurn(1, 0.0, 2.0)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav(), _silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True
    )

    assert result.text == "Speaker 1: hello"
    assert result.notes == ["Chunk 1 of 2 timed out; its text may be incomplete or missing."]


@pytest.mark.asyncio
async def test_speaker_requests_wait_for_each_other_and_the_timeout_starts_after(monkeypatch):
    """A second upload queued behind another's speaker detection does not spend its timeout waiting."""
    import asyncio

    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn

    transcriber = _DiarizingTranscriber([("hi", [Word("hi", 0.1, 0.2)], True)], [SpeakerTurn(0, 0.0, 1.0)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    assert ft._SERVER_LOCK.acquire(blocking=False)  # another upload's request is running
    try:
        task = asyncio.create_task(
            ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)
        )
        await asyncio.sleep(0.5)
        assert transcriber.diarize_calls == [] and not task.done()
    finally:
        ft._SERVER_LOCK.release()

    result = await asyncio.wait_for(task, timeout=5)

    assert len(transcriber.diarize_calls) == 1
    assert result.text == "Speaker 1: hi"


@pytest.mark.asyncio
async def test_cancelling_during_speaker_detection_frees_the_server_slot(monkeypatch):
    import asyncio

    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn

    class Hanging(_DiarizingTranscriber):
        async def diarize(self, wav, timeout):
            self.diarize_calls.append((b"", timeout))
            await asyncio.sleep(60)

    transcriber = Hanging([("hi", [Word("hi", 0.1, 0.2)], True)], [SpeakerTurn(0, 0.0, 1.0)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)
    task = asyncio.create_task(
        ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)
    )
    for _ in range(50):
        if transcriber.diarize_calls:
            break
        await asyncio.sleep(0.1)
    assert transcriber.diarize_calls  # the job is inside speaker detection and holds the slot
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert ft._SERVER_LOCK.acquire(blocking=False)
    ft._SERVER_LOCK.release()


@pytest.mark.asyncio
async def test_chunks_wait_for_another_jobs_speaker_request(monkeypatch):
    """A chunk of one upload never spends its timeout behind another job's speaker request."""
    import asyncio

    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _DiarizingTranscriber([("hi", [Word("hi", 0.1, 0.2)], True)], [])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)
    assert ft._SERVER_LOCK.acquire(blocking=False)  # another job's speaker request is running
    try:
        task = asyncio.create_task(ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b")))
        await asyncio.sleep(0.5)
        assert not task.done()
    finally:
        ft._SERVER_LOCK.release()
    result = await asyncio.wait_for(task, timeout=5)
    assert result.text == "hi"


def test_file_extension_keeps_a_name_that_is_only_an_extension():
    from nexusvox.transcription.file_transcribe import file_extension

    assert file_extension("test.wav") == ".wav"
    assert file_extension("music.MP3") == ".mp3"
    assert file_extension(".mp3") == ".mp3"
    assert file_extension("noext") == ""
    assert file_extension("multi.dots.flac") == ".flac"


@pytest.mark.asyncio
async def test_diarize_with_a_model_without_speaker_detection_says_so(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _DiarizingTranscriber([("hello", [], True)], [])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="whisper-large-v3-turbo"), diarize=True)

    assert transcriber.diarize_calls == []
    assert transcriber.word_timestamps is False
    assert result.text == "hello"
    assert result.notes == [
        "Speaker detection needs Parakeet TDT 0.6B as the active model; the transcript has no speaker labels."
    ]


@pytest.mark.asyncio
async def test_the_missing_speaker_detection_note_can_be_left_to_the_caller(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _DiarizingTranscriber([("hello", [], True)], [])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file(
        [_silent_wav()], InferenceConfig(model="whisper-large-v3-turbo"), diarize=True, explain_no_speakers=False
    )

    assert result.text == "hello"
    assert result.notes == []


@pytest.mark.asyncio
async def test_model_with_speaker_detection_but_a_client_without_it_is_not_told_to_switch(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _DiarizingTranscriber([("hello", [Word("hello", 0.1, 0.4)], True)], [])
    transcriber.supports_diarization = False
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)

    assert result.notes == [
        "Speaker detection is not available with this server; the transcript has no speaker labels."
    ]


@pytest.mark.asyncio
async def test_progress_is_reported_per_chunk(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig

    transcriber = _ScriptedTranscriber([("a", [], True), ("b", [], True), ("c", [], True)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)
    seen = []

    await ft.transcribe_file(
        [_silent_wav(), _silent_wav(), _silent_wav()],
        InferenceConfig(model="whisper-large-v3-turbo"),
        device="cpu",
        on_progress=lambda *args: seen.append(args),
    )

    assert seen == [("transcribing", 1, 3), ("transcribing", 2, 3), ("transcribing", 3, 3)]


@pytest.mark.asyncio
async def test_a_job_that_wants_speakers_waits_for_a_cold_server_to_warm_up(monkeypatch):
    """A fresh transcriber starts cold: the job waits for the warm-up instead of running on the CPU without speakers."""
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn, Word

    script = [("Hello there.", [Word("Hello", 0.1, 0.3), Word("there.", 0.35, 0.6)], True)]
    transcriber = _DiarizingTranscriber(script, [SpeakerTurn(0, 0.0, 1.0)])
    waited: list[bool] = []

    async def wait_until_warm():
        waited.append(True)

    transcriber.wait_until_warm = wait_until_warm
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav(1.0)], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)
    assert waited == [True]
    assert result.text.startswith("Speaker 1:")

    waited.clear()
    transcriber._script.append(("Again.", [Word("Again.", 0.1, 0.3)], True))
    await ft.transcribe_file([_silent_wav(1.0)], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=False)
    assert waited == []  # plain transcription does not wait


@pytest.mark.asyncio
async def test_speaker_detection_is_not_requested_when_words_are_incomplete(monkeypatch):
    import nexusvox.transcription.file_transcribe as ft
    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.transcriber import SpeakerTurn

    transcriber = _DiarizingTranscriber([("hello there", [Word("hello", 0.1, 0.2)], True)], [SpeakerTurn(0, 0.0, 1.0)])
    monkeypatch.setattr(ft, "create_transcriber", lambda *a, **kw: transcriber)

    result = await ft.transcribe_file([_silent_wav()], InferenceConfig(model="parakeet-tdt-0.6b"), diarize=True)

    assert transcriber.diarize_calls == []
    assert any("without timing" in n for n in result.notes)
