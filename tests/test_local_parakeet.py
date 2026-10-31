"""Tests for the in-process Parakeet transcriber (onnx-asr, int8, CPU).

The onnx-asr model is replaced through `LocalParakeetTranscriber._get_model` / `_get_vad`, so
these tests need neither onnx-asr's weights nor a download.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from nexusvox.config import InferenceConfig, model_needs_docker
from nexusvox.transcriber import create_transcriber
from nexusvox.transcribers import local_parakeet
from nexusvox.transcribers.local_parakeet import FRAME_S, LocalParakeetTranscriber, words_from_tokens

SAMPLE_RATE = 16_000


@dataclass
class _Result:
    text: str
    tokens: list[str] | None = None
    timestamps: list[float] | None = None


@dataclass
class _Segment(_Result):
    start: float = 0.0
    end: float = 0.0


class _FakeAdapter:
    """What `model` and `model.with_timestamps()` look like to the engine."""

    def __init__(self, model: _FakeModel, *, timed: bool = False) -> None:
        self._model = model
        self._timed = timed

    def with_timestamps(self):
        return _FakeAdapter(self._model, timed=True)

    def recognize(self, samples, *, sample_rate):
        assert sample_rate == SAMPLE_RATE
        assert samples.dtype == np.float32
        self._model.calls.append({"timed": self._timed, "seconds": len(samples) / SAMPLE_RATE})
        result = self._model.results.pop(0) if self._model.results else self._model.result
        return result if self._timed else result.text


class _FakeModel(_FakeAdapter):
    def __init__(self, result: _Result | None = None, results: list[_Result] | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result or _Result("hello world", [" hello", " world"], [0.0, 0.4])
        self.results = list(results or [])
        super().__init__(self)


@pytest.fixture(autouse=True)
def clean_class_state():
    LocalParakeetTranscriber._model_cache.clear()
    LocalParakeetTranscriber._set_status("idle")
    LocalParakeetTranscriber._preloading = False
    yield
    LocalParakeetTranscriber._model_cache.clear()
    LocalParakeetTranscriber._set_status("idle")
    LocalParakeetTranscriber._preloading = False


def _install(monkeypatch, model: _FakeModel) -> None:
    monkeypatch.setattr(LocalParakeetTranscriber, "_get_model", classmethod(lambda cls, *a: model))


def _make() -> LocalParakeetTranscriber:
    return LocalParakeetTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))


def _pcm(seconds: float, amplitude: int = 1000) -> bytes:
    return (np.full(int(seconds * SAMPLE_RATE), amplitude, dtype=np.int16)).tobytes()


async def _run(t: LocalParakeetTranscriber, seconds: float):
    await t.connect()
    await t.send_audio_chunk(_pcm(seconds))
    return await t.finish()


# ---- Engine selection --------------------------------------------------------


def test_factory_returns_local_parakeet_on_cpu():
    t = create_transcriber(InferenceConfig(model="parakeet-tdt-0.6b"), device="cpu")

    assert isinstance(t, LocalParakeetTranscriber)
    assert t.needs_docker is False
    assert t.model == "nvidia/parakeet-tdt-0.6b-v3"
    assert t.supports_word_timestamps is True
    assert t.supports_diarization is False


def test_factory_keeps_the_docker_client_on_gpu():
    from nexusvox.transcriber import OpenAIHttpTranscriber

    t = create_transcriber(InferenceConfig(model="parakeet-tdt-0.6b"), device="cuda")

    assert isinstance(t, OpenAIHttpTranscriber)
    assert t.needs_docker is True


def test_whisper_still_gets_the_whisper_engine_on_cpu():
    from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber

    assert isinstance(create_transcriber(InferenceConfig(model="whisper-small"), device="cpu"), LocalWhisperTranscriber)


@pytest.mark.parametrize(("device", "expected"), [("cpu", False), ("cuda", True)])
def test_docker_is_only_needed_on_gpu(device, expected):
    assert model_needs_docker("parakeet-tdt-0.6b", device) is expected


def test_the_model_is_the_int8_onnx_build_of_v3():
    from nexusvox.config import MODEL_REGISTRY

    t = _make()

    assert t._onnx_model == "nemo-parakeet-tdt-0.6b-v3"
    assert t._quantization == "int8"
    assert MODEL_REGISTRY["parakeet-tdt-0.6b"]["requires_gpu"] is False


# ---- Words from tokens -------------------------------------------------------


def test_words_start_at_tokens_with_the_word_mark():
    tokens = [" Hel", "lo", " wor", "ld", "."]
    stamps = [0.0, 0.16, 0.8, 0.88, 1.04]

    words = words_from_tokens(tokens, stamps)

    assert [w.word for w in words] == ["Hello", "world."]
    assert words[0].start == 0.0
    assert words[1].start == 0.8


def test_the_sentencepiece_mark_works_like_a_space():
    words = words_from_tokens(["▁Gu", "ten", "▁Tag"], [0.1, 0.2, 0.9])

    assert [w.word for w in words] == ["Guten", "Tag"]


def test_a_word_ends_one_frame_after_its_last_token_but_not_after_the_next_word_starts():
    words = words_from_tokens([" a", "b", " c", " d"], [0.0, 0.08, 0.2, 0.24])

    assert words[0].end == pytest.approx(0.08 + FRAME_S)  # 0.16, before "c" at 0.2
    assert words[1].end == pytest.approx(0.24)  # capped by "d"
    assert words[2].end == pytest.approx(0.24 + FRAME_S)  # the last word keeps its frame


def test_a_bare_word_mark_only_breaks_the_word():
    words = words_from_tokens([" go", "▁", "on"], [0.0, 0.1, 0.2])

    assert [w.word for w in words] == ["go", "on"]


def test_the_first_token_opens_a_word_even_without_a_mark():
    assert [w.word for w in words_from_tokens(["Hi", " you"], [0.5, 0.9])] == ["Hi", "you"]


def test_offset_moves_every_word_onto_the_file_timeline():
    words = words_from_tokens([" a", " b"], [0.0, 0.5], offset_s=10.0)

    assert [(w.start) for w in words] == [10.0, 10.5]
    assert all(w.end >= w.start for w in words)


def test_punctuation_with_the_word_mark_stays_on_the_word_before_it():
    # onnx-asr writes "Hello, world - ok" for these tokens: no space before a non-word character.
    tokens = [" Hello", " ,", " world", " -", " ok"]

    words = words_from_tokens(tokens, [0.0, 0.3, 0.5, 0.9, 1.1])

    assert [w.word for w in words] == ["Hello,", "world-", "ok"]
    assert words[0].end <= words[1].start


def test_a_bare_word_mark_before_punctuation_does_not_open_a_word():
    words = words_from_tokens([" yes", "\u2581", ",", " no"], [0.0, 0.2, 0.3, 0.6])

    assert [w.word for w in words] == ["yes,", "no"]


def test_the_words_match_the_text_word_count_for_a_sentence_with_punctuation():
    tokens = [" Wait", " ,", " what", " ?", " Fine", " ."]

    assert len(words_from_tokens(tokens, [0.0, 0.2, 0.4, 0.6, 0.9, 1.1])) == len("Wait, what? Fine.".split())


def test_no_tokens_no_words():
    assert words_from_tokens([], []) == []


def test_german_words_with_umlauts_and_numbers():
    tokens = [" Gr", "ü", "ße", " 25", " Euro"]
    words = words_from_tokens(tokens, [0.0, 0.1, 0.2, 0.5, 0.9])

    assert [w.word for w in words] == ["Grüße", "25", "Euro"]


# ---- Dictation ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_short_dictation_is_one_model_call_as_one_call(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)

    result = await _run(_make(), 3.0)

    assert result.done is True
    assert result.text == "hello world"
    assert result.words == []  # not asked for
    assert model.calls == [{"timed": False, "seconds": 3.0}]


@pytest.mark.asyncio
async def test_word_timestamps_ask_the_model_for_tokens(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)
    t = _make()
    t.word_timestamps = True

    result = await _run(t, 3.0)

    assert model.calls == [{"timed": True, "seconds": 3.0}]
    assert [w.word for w in result.words] == ["hello", "world"]
    assert result.words[1].start == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_audio_at_the_limit_still_goes_in_one_call(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)

    await _run(_make(), local_parakeet.MAX_DIRECT_S)

    assert [c["timed"] for c in model.calls] == [False]


@pytest.mark.asyncio
async def test_silence_shorter_than_a_tenth_of_a_second_never_reaches_the_model(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)

    result = await _run(_make(), 0.05)

    assert result.done is True
    assert result.text == ""
    assert model.calls == []


@pytest.mark.asyncio
async def test_an_odd_trailing_byte_is_ignored(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)
    t = _make()

    await t.connect()
    await t.send_audio_chunk(_pcm(1.0) + b"\x01")
    result = await t.finish()

    assert result.text == "hello world"


@pytest.mark.asyncio
async def test_finish_requires_connect():
    with pytest.raises(RuntimeError, match="Not connected"):
        await _make().finish()


@pytest.mark.asyncio
async def test_send_requires_connect():
    with pytest.raises(RuntimeError, match="Not connected"):
        await _make().send_audio_chunk(b"\x00\x00")


@pytest.mark.asyncio
async def test_the_buffer_is_cleared_between_recordings(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)
    t = _make()

    await _run(t, 2.0)
    await _run(t, 1.0)

    assert [c["seconds"] for c in model.calls] == [2.0, 1.0]


# ---- Long audio (file uploads) ----------------------------------------------

SR = SAMPLE_RATE


def _speech_with_pauses(pause_at_s: list[float], total_s: float, pause_s: float = 0.5) -> np.ndarray:
    """Loud noise-like 'speech' with silent gaps of `pause_s` centred on `pause_at_s`."""
    rng = np.random.default_rng(0)
    x = rng.uniform(-0.3, 0.3, int(total_s * SR)).astype(np.float32)
    for at in pause_at_s:
        x[int((at - pause_s / 2) * SR) : int((at + pause_s / 2) * SR)] = 0.0
    return x


def _seconds(ranges):
    return [(round(a / SR, 2), round(b / SR, 2)) for a, b in ranges]


async def _run_samples(t, x):
    await t.connect()
    await t.send_audio_chunk((x * 32767).astype(np.int16).tobytes())
    return await t.finish()


def test_audio_up_to_the_limit_is_not_cut():
    x = _speech_with_pauses([5.0], local_parakeet.MAX_DIRECT_S)
    assert local_parakeet.split_at_pauses(x) == [(0, len(x))]


def test_long_audio_is_cut_in_the_middle_of_a_pause():
    x = _speech_with_pauses([14.0], 30.0)
    assert _seconds(local_parakeet.split_at_pauses(x)) == [(0.0, 14.0), (14.0, 30.0)]


def test_the_longest_pause_wins():
    x = _speech_with_pauses([10.0], 40.0, pause_s=0.3)
    x[int(16.0 * SR) : int(17.0 * SR)] = 0.0  # a 1 s pause at 16.5 s
    first = local_parakeet.split_at_pauses(x)[0]
    assert abs(first[1] / SR - 16.5) < 0.05


def test_segments_stay_between_the_minimum_and_the_maximum_length():
    x = _speech_with_pauses([4.0, 9.0, 21.0, 33.0, 47.0, 58.0, 70.0], 85.0, pause_s=0.4)
    ranges = local_parakeet.split_at_pauses(x)
    lengths = [(b - a) / SR for a, b in ranges]
    assert all(n <= local_parakeet.MAX_DIRECT_S + 1e-6 for n in lengths)
    assert all(n >= local_parakeet.MIN_SEGMENT_S for n in lengths[:-1])
    assert ranges[0][0] == 0 and ranges[-1][1] == len(x)
    assert all(ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1))


def test_a_pause_before_the_minimum_length_is_not_used():
    x = _speech_with_pauses([2.0], 30.0)  # the only pause would give a 2 s segment
    first = local_parakeet.split_at_pauses(x)[0]
    assert (first[1] - first[0]) / SR >= local_parakeet.MIN_SEGMENT_S


def test_a_long_clip_without_pauses_is_still_bounded():
    x = np.random.default_rng(1).uniform(-0.3, 0.3, 70 * SR).astype(np.float32)
    ranges = local_parakeet.split_at_pauses(x)
    assert len(ranges) >= 4
    assert all((b - a) / SR <= local_parakeet.MAX_DIRECT_S + 1e-6 for a, b in ranges)
    assert sum(b - a for a, b in ranges) == len(x)


def test_a_pauseless_clip_is_cut_at_its_quietest_spot():
    x = np.random.default_rng(2).uniform(-0.3, 0.3, 30 * SR).astype(np.float32)
    x[int(12.0 * SR) : int(12.02 * SR)] *= 0.01  # one soft frame, too short to be a pause
    first = local_parakeet.split_at_pauses(x)[0]
    assert abs(first[1] / SR - 12.01) < 0.03


def test_background_noise_does_not_hide_the_pauses():
    x = _speech_with_pauses([13.0], 30.0)
    mask = x == 0.0
    x[mask] = np.random.default_rng(3).uniform(-0.004, 0.004, int(mask.sum()))
    first = local_parakeet.split_at_pauses(x)[0]
    assert abs(first[1] / SR - 13.0) < 0.3


@pytest.mark.asyncio
async def test_long_audio_is_cut_at_pauses_and_the_segments_are_joined(monkeypatch):
    model = _FakeModel(results=[_Result("first part"), _Result("second part")])
    _install(monkeypatch, model)

    result = await _run_samples(_make(), _speech_with_pauses([14.0], 30.0))

    assert result.text == "first part second part"
    assert [c["seconds"] for c in model.calls] == [14.0, 16.0]
    assert all(c["timed"] is False for c in model.calls)


@pytest.mark.asyncio
async def test_segment_words_are_shifted_by_the_segment_start(monkeypatch):
    model = _FakeModel(
        results=[_Result("alpha beta", [" alpha", " beta"], [0.0, 0.5]), _Result("gamma", [" gamma"], [0.25])]
    )
    _install(monkeypatch, model)
    t = _make()
    t.word_timestamps = True

    result = await _run_samples(t, _speech_with_pauses([14.0], 30.0))

    assert [(w.word, w.start) for w in result.words] == [("alpha", 0.0), ("beta", 0.5), ("gamma", 14.25)]
    assert len(result.words) == len(result.text.split())


@pytest.mark.asyncio
async def test_long_audio_without_speech_gives_an_empty_transcript(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)

    result = await _run_samples(_make(), np.zeros(30 * SR, dtype=np.float32))

    assert result.done is True
    assert result.text == ""
    assert result.words == []
    assert model.calls == []


@pytest.mark.asyncio
async def test_a_silent_segment_is_not_decoded_and_adds_no_stray_space(monkeypatch):
    model = _FakeModel(result=_Result("word"))
    _install(monkeypatch, model)
    x = _speech_with_pauses([], 60.0)
    x[int(20 * SR) : int(40 * SR)] = 0.0  # a stretch of pure silence
    voiced = [(a, b) for a, b in local_parakeet.split_at_pauses(x) if np.max(np.abs(x[a:b])) > 0.01]
    assert 0 < len(voiced) < len(local_parakeet.split_at_pauses(x))

    result = await _run_samples(_make(), x)

    assert len(model.calls) == len(voiced)
    assert result.text == " ".join(["word"] * len(voiced))


@pytest.mark.asyncio
async def test_transcribe_file_cuts_a_long_chunk_at_pauses(monkeypatch):
    """The upload path: 60 s chunks reach the engine as one recording each."""
    import io
    import wave

    import nexusvox.file_transcribe as ft

    model = _FakeModel(result=_Result("spoken", [" spoken"], [0.0]))
    _install(monkeypatch, model)

    pcm = (_speech_with_pauses([14.0], 40.0) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)

    result = await ft.transcribe_file(
        [buf.getvalue()], InferenceConfig(model="parakeet-tdt-0.6b"), device="cpu", word_timestamps=True
    )

    assert result.text == "spoken spoken spoken"
    assert [w.start for w in result.words][:2] == [0.0, 14.0]
    assert all(c["timed"] is True and c["seconds"] <= local_parakeet.MAX_DIRECT_S for c in model.calls)
    assert result.notes == []


@pytest.mark.asyncio
async def test_speaker_detection_on_cpu_returns_the_plain_transcript_with_the_gpu_note(monkeypatch):
    import io
    import wave

    import nexusvox.file_transcribe as ft

    _install(monkeypatch, _FakeModel(result=_Result("hi there", [" hi", " there"], [0.0, 0.3])))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(_pcm(2.0))

    result = await ft.transcribe_file(
        [buf.getvalue()], InferenceConfig(model="parakeet-tdt-0.6b"), device="cpu", diarize=True
    )

    assert result.text == "hi there"
    assert [w.word for w in result.words] == ["hi", "there"]
    assert result.notes == ["Speaker detection needs the GPU container; the transcript has no speaker labels."]
    assert all(w.speaker is None for w in result.words)


# ---- Timeout, caching, status -----------------------------------------------


@pytest.mark.asyncio
async def test_a_timed_out_recognition_is_not_done(monkeypatch):
    import time

    class _Slow(_FakeModel):
        def recognize(self, samples, *, sample_rate):
            time.sleep(0.5)
            return "late"

    _install(monkeypatch, _Slow())
    t = _make()
    await t.connect()
    await t.send_audio_chunk(_pcm(1.0))

    result = await t.finish(timeout=0.05)

    assert result.done is False
    assert result.text == ""


def test_the_model_is_loaded_once_and_shared(monkeypatch):
    loaded: list[tuple] = []

    class _Module:
        @staticmethod
        def load_model(name, **kwargs):
            loaded.append((name, kwargs))
            return _FakeModel()

    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", _Module)
    monkeypatch.setattr(LocalParakeetTranscriber, "_is_downloaded", staticmethod(lambda: True))

    first = LocalParakeetTranscriber._get_model("nemo-parakeet-tdt-0.6b-v3", "int8")
    second = LocalParakeetTranscriber._get_model("nemo-parakeet-tdt-0.6b-v3", "int8")

    assert first is second
    assert loaded == [("nemo-parakeet-tdt-0.6b-v3", {"quantization": "int8", "providers": ["CPUExecutionProvider"]})]
    assert LocalParakeetTranscriber.load_status() == {"state": "ready", "error": None}


def test_a_missing_download_is_reported_while_it_runs(monkeypatch):
    seen: list[str] = []

    class _Module:
        @staticmethod
        def load_model(name, **kwargs):
            seen.append(LocalParakeetTranscriber.load_status()["state"])
            return _FakeModel()

    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", _Module)
    monkeypatch.setattr(LocalParakeetTranscriber, "_is_downloaded", staticmethod(lambda: False))

    LocalParakeetTranscriber._get_model("m", "int8")

    assert seen == ["downloading"]


def test_a_cached_model_reports_loading(monkeypatch):
    seen: list[str] = []

    class _Module:
        @staticmethod
        def load_model(name, **kwargs):
            seen.append(LocalParakeetTranscriber.load_status()["state"])
            return _FakeModel()

    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", _Module)
    monkeypatch.setattr(LocalParakeetTranscriber, "_is_downloaded", staticmethod(lambda: True))

    LocalParakeetTranscriber._get_model("m", "int8")

    assert seen == ["loading"]


def test_a_failed_load_is_reported_and_not_cached(monkeypatch):
    class _Module:
        @staticmethod
        def load_model(name, **kwargs):
            raise OSError("no network")

    monkeypatch.setitem(__import__("sys").modules, "onnx_asr", _Module)
    monkeypatch.setattr(LocalParakeetTranscriber, "_is_downloaded", staticmethod(lambda: False))

    with pytest.raises(OSError, match="no network"):
        LocalParakeetTranscriber._get_model("m", "int8")

    assert LocalParakeetTranscriber.load_status() == {"state": "error", "error": "no network"}
    assert LocalParakeetTranscriber._model_cache == {}


def test_the_status_can_be_read_while_a_download_holds_the_cache_lock():
    LocalParakeetTranscriber._set_status("downloading")

    with LocalParakeetTranscriber._cache_lock:
        assert LocalParakeetTranscriber.load_status()["state"] == "downloading"


@pytest.mark.asyncio
async def test_connect_starts_a_background_load_only_when_the_model_is_missing(monkeypatch):
    import asyncio

    loads: list[tuple] = []
    monkeypatch.setattr(
        LocalParakeetTranscriber, "_get_model", classmethod(lambda cls, *a: loads.append(a) or _FakeModel())
    )
    t = _make()

    await t.connect()
    await asyncio.sleep(0.1)
    assert loads == [("nemo-parakeet-tdt-0.6b-v3", "int8")]

    LocalParakeetTranscriber._model_cache[("nemo-parakeet-tdt-0.6b-v3", "int8")] = object()
    await t.connect()
    await asyncio.sleep(0.1)
    assert len(loads) == 1


@pytest.mark.asyncio
async def test_connect_queues_one_background_load_at_a_time(monkeypatch):
    import asyncio
    import threading

    release = threading.Event()
    loads: list[tuple] = []

    def slow_load(cls, *a):
        loads.append(a)
        release.wait(2)
        return _FakeModel()

    monkeypatch.setattr(LocalParakeetTranscriber, "_get_model", classmethod(slow_load))
    monkeypatch.setattr(LocalParakeetTranscriber, "_is_downloaded", staticmethod(lambda: False))

    for _ in range(4):  # four hotkey presses while the first download runs
        await _make().connect()
    await asyncio.sleep(0.1)
    assert len(loads) == 1
    assert LocalParakeetTranscriber.load_status()["state"] == "downloading"  # visible at once, before the load ends

    release.set()
    await asyncio.sleep(0.2)
    assert LocalParakeetTranscriber._preloading is False


@pytest.mark.asyncio
async def test_connect_does_not_retry_a_failed_load_the_finish_retries_once(monkeypatch):
    import asyncio

    attempts: list[str] = []

    def failing(cls, *a):
        attempts.append("load")
        cls._set_status("error", "offline")
        raise OSError("offline")

    monkeypatch.setattr(LocalParakeetTranscriber, "_get_model", classmethod(failing))
    t = _make()
    LocalParakeetTranscriber._set_status("error", "offline")

    await t.connect()
    await asyncio.sleep(0.1)
    assert attempts == []  # connect leaves the retry to finish

    await t.send_audio_chunk(_pcm(1.0))
    with pytest.raises(OSError, match="offline"):
        await t.finish()
    assert attempts == ["load"]


def test_a_new_transcriber_clears_an_earlier_failed_load():
    LocalParakeetTranscriber._set_status("error", "offline")

    _make()

    assert LocalParakeetTranscriber.load_status()["state"] == "idle"


@pytest.mark.asyncio
async def test_a_load_error_resets_the_transcriber_and_propagates(monkeypatch):
    def failing(cls, *a):
        raise OSError("corrupt cache")

    monkeypatch.setattr(LocalParakeetTranscriber, "_get_model", classmethod(failing))
    t = _make()
    await t.connect()
    await t.send_audio_chunk(_pcm(1.0))

    with pytest.raises(OSError, match="corrupt cache"):
        await t.finish()

    assert t.connected is False
    assert t._audio_buffer == bytearray()


@pytest.mark.asyncio
async def test_a_job_that_timed_out_stops_and_does_not_hold_up_the_next_dictation(monkeypatch):
    import asyncio
    import threading

    entered = threading.Event()
    gate = threading.Event()
    calls: list[str] = []

    class _Blocking(_FakeModel):
        def recognize(self, samples, *, sample_rate):
            calls.append("recognize")
            entered.set()
            gate.wait(5)
            return "slow result"

    monkeypatch.setattr(LocalParakeetTranscriber, "_get_model", classmethod(lambda cls, *a: _Blocking()))

    first = _make()
    await first.connect()
    await first.send_audio_chunk(_pcm(1.0))
    running = asyncio.create_task(first.finish(timeout=10))
    assert await asyncio.to_thread(entered.wait, 5)  # the single worker is inside the model call

    # Two more dictations queue behind it and give up; their jobs must not run once the worker is free.
    queued = []
    for _ in range(2):
        t = _make()
        await t.connect()
        await t.send_audio_chunk(_pcm(1.0))
        queued.append(await t.finish(timeout=0.05))
    assert [r.done for r in queued] == [False, False]

    gate.set()
    assert (await running).text == "slow result"
    await asyncio.to_thread(LocalParakeetTranscriber._executor.submit(lambda: None).result, 5)  # queue drained
    assert calls == ["recognize"]  # the cancelled jobs never reached the model


def test_other_engines_report_no_load_status():
    from nexusvox.transcriber import OpenAIHttpTranscriber

    assert OpenAIHttpTranscriber(InferenceConfig(model="cohere-transcribe")).load_status() is None


# ---- App and dashboard integration ------------------------------------------


def _bare_app(transcriber):
    try:
        from nexusvox.app import NexusVoxApp
    except Exception:  # Windows-only imports on other platforms
        pytest.skip("nexusvox.app needs the Windows runtime")
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._switch_status = "idle"
    app._switch_error = None
    app._transcriber = transcriber
    return app


def test_parakeet_stays_the_model_on_cpu_but_the_gpu_only_models_fall_back():
    try:
        from nexusvox.app import apply_startup_fallback
    except Exception:
        pytest.skip("nexusvox.app needs the Windows runtime")

    parakeet = InferenceConfig(model="parakeet-tdt-0.6b")
    apply_startup_fallback(parakeet, "cpu")
    voxtral = InferenceConfig(model="voxtral-mini-4b")
    apply_startup_fallback(voxtral, "cpu")

    assert parakeet.model == "parakeet-tdt-0.6b"
    assert voxtral.model == "whisper-large-v3-turbo"


@pytest.mark.parametrize("state", ["downloading", "loading"])
def test_the_dashboard_sees_the_model_load_while_no_switch_runs(state):
    LocalParakeetTranscriber._set_status(state)

    assert _bare_app(_make()).get_switch_status() == {"status": state, "error": None}


def test_a_finished_switch_does_not_hide_a_running_download():
    LocalParakeetTranscriber._set_status("downloading")
    app = _bare_app(_make())
    app._switch_status = "ready"  # switch_model sets this right before the preload starts

    assert app.get_switch_status() == {"status": "downloading", "error": None}


@pytest.mark.parametrize("switch_status", ["idle", "ready"])
def test_a_failed_load_is_reported_with_its_reason(switch_status):
    app = _bare_app(_make())
    LocalParakeetTranscriber._set_status("error", "HTTP 503 from huggingface.co")
    app._switch_status = switch_status

    assert app.get_switch_status() == {"status": "error", "error": "HTTP 503 from huggingface.co"}


def test_a_ready_model_leaves_the_switch_status_alone():
    LocalParakeetTranscriber._set_status("ready")

    assert _bare_app(_make()).get_switch_status() == {"status": "idle", "error": None}


def test_a_running_switch_wins_over_the_model_load():
    LocalParakeetTranscriber._set_status("downloading")
    app = _bare_app(_make())
    app._switch_status = "stopping"

    assert app.get_switch_status() == {"status": "stopping", "error": None}


def test_engines_without_a_load_status_never_change_the_switch_status():
    from nexusvox.transcriber import OpenAIHttpTranscriber

    app = _bare_app(OpenAIHttpTranscriber(InferenceConfig(model="cohere-transcribe")))

    assert app.get_switch_status() == {"status": "idle", "error": None}


def _dashboard_client(db, session_factory, monkeypatch, device, *, running=None):
    """A dashboard whose app runs `running` (default: the engine `device` selects)."""
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.inference.device = device
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: d if d in ("cuda", "cpu") else "cpu")
    transcriber = running or create_transcriber(config.inference, device=device)
    api = DashboardAPI(session_factory, config, db=db, get_transcriber=lambda: transcriber)
    app = _create_app(api)
    app.config["TESTING"] = True
    return app.test_client()


def test_speaker_detection_is_off_for_parakeet_on_cpu_with_the_gpu_hint(db, session_factory, monkeypatch):
    with _dashboard_client(db, session_factory, monkeypatch, "cpu") as client:
        data = client.get("/api/models/current").get_json()

    assert data["model"] == "parakeet-tdt-0.6b"
    assert data["diarization"] is False
    assert data["diarization_hint"] == "Speaker detection needs the GPU container"


def test_speaker_detection_stays_on_for_parakeet_on_gpu(db, session_factory, monkeypatch):
    with _dashboard_client(db, session_factory, monkeypatch, "cuda") as client:
        data = client.get("/api/models/current").get_json()

    assert data["diarization"] is True
    assert data["diarization_hint"] == ""


def test_speaker_detection_follows_the_running_engine_not_the_device_setting(db, session_factory, monkeypatch):
    """The setting says CPU but needs a restart: the container is still what runs, so it can still detect speakers."""
    from nexusvox.transcriber import OpenAIHttpTranscriber

    container = OpenAIHttpTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))
    with _dashboard_client(db, session_factory, monkeypatch, "cpu", running=container) as client:
        data = client.get("/api/models/current").get_json()

    assert data["diarization"] is True
    assert data["diarization_hint"] == ""


def test_the_current_model_does_not_probe_for_a_gpu(db, session_factory, monkeypatch):
    with _dashboard_client(db, session_factory, monkeypatch, "cuda") as client:
        monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: pytest.fail("the device was probed"))
        assert client.get("/api/models/current").status_code == 200


def test_parakeet_is_listed_and_switchable_on_cpu(db, session_factory, monkeypatch):
    with _dashboard_client(db, session_factory, monkeypatch, "cpu") as client:
        models = {m["id"]: m for m in client.get("/api/models").get_json()}

    assert models["parakeet-tdt-0.6b"]["inprocess_supported"] is True
    assert models["parakeet-tdt-0.6b"]["requires_gpu"] is False
    assert "voxtral-mini-4b" not in models
