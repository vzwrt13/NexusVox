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
    """What `model`, `model.with_timestamps()` and `model.with_vad(...)` look like to the engine."""

    def __init__(self, model: _FakeModel, *, vad: bool = False, timed: bool = False) -> None:
        self._model = model
        self._vad = vad
        self._timed = timed

    def with_timestamps(self):
        return _FakeAdapter(self._model, vad=self._vad, timed=True)

    def recognize(self, samples, *, sample_rate):
        assert sample_rate == SAMPLE_RATE
        assert samples.dtype == np.float32
        self._model.calls.append({"vad": self._vad, "timed": self._timed, "seconds": len(samples) / SAMPLE_RATE})
        if self._vad:
            return iter(self._model.segments)
        if self._timed:
            return self._model.result
        return self._model.result.text


class _FakeModel(_FakeAdapter):
    def __init__(self, result: _Result | None = None, segments: list[_Segment] | None = None) -> None:
        self.calls: list[dict] = []
        self.vad_args: list[dict] = []
        self.result = result or _Result("hello world", [" hello", " world"], [0.0, 0.4])
        self.segments = segments or []
        super().__init__(self)

    def with_vad(self, vad, **kwargs):
        self.vad_args.append({"vad": vad, **kwargs})
        return _FakeAdapter(self, vad=True)


@pytest.fixture(autouse=True)
def clean_class_state():
    LocalParakeetTranscriber._model_cache.clear()
    LocalParakeetTranscriber._vad_cache.clear()
    LocalParakeetTranscriber._set_status("idle")
    yield
    LocalParakeetTranscriber._model_cache.clear()
    LocalParakeetTranscriber._vad_cache.clear()
    LocalParakeetTranscriber._set_status("idle")


def _install(monkeypatch, model: _FakeModel) -> object:
    vad = object()
    monkeypatch.setattr(LocalParakeetTranscriber, "_get_model", classmethod(lambda cls, *a: model))
    monkeypatch.setattr(LocalParakeetTranscriber, "_get_vad", classmethod(lambda cls: vad))
    return vad


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


def test_no_tokens_no_words():
    assert words_from_tokens([], []) == []


def test_german_words_with_umlauts_and_numbers():
    tokens = [" Gr", "ü", "ße", " 25", " Euro"]
    words = words_from_tokens(tokens, [0.0, 0.1, 0.2, 0.5, 0.9])

    assert [w.word for w in words] == ["Grüße", "25", "Euro"]


# ---- Dictation ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_short_dictation_is_one_model_call_without_vad(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)

    result = await _run(_make(), 3.0)

    assert result.done is True
    assert result.text == "hello world"
    assert result.words == []  # not asked for
    assert model.calls == [{"vad": False, "timed": False, "seconds": 3.0}]
    assert model.vad_args == []


@pytest.mark.asyncio
async def test_word_timestamps_ask_the_model_for_tokens(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)
    t = _make()
    t.word_timestamps = True

    result = await _run(t, 3.0)

    assert model.calls == [{"vad": False, "timed": True, "seconds": 3.0}]
    assert [w.word for w in result.words] == ["hello", "world"]
    assert result.words[1].start == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_audio_at_the_limit_still_goes_in_one_call(monkeypatch):
    model = _FakeModel()
    _install(monkeypatch, model)

    await _run(_make(), local_parakeet.MAX_DIRECT_S)

    assert [c["vad"] for c in model.calls] == [False]


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


@pytest.mark.asyncio
async def test_long_audio_is_cut_by_the_vad_and_the_segments_are_joined(monkeypatch):
    segments = [
        _Segment("first part", start=1.0, end=15.0),
        _Segment("second part", start=20.0, end=38.0),
    ]
    model = _FakeModel(segments=segments)
    vad = _install(monkeypatch, model)

    result = await _run(_make(), 45.0)

    assert result.text == "first part second part"
    assert model.calls == [{"vad": True, "timed": False, "seconds": 45.0}]
    assert model.vad_args == [{"vad": vad, "max_speech_duration_s": local_parakeet.MAX_DIRECT_S}]


@pytest.mark.asyncio
async def test_segment_words_are_shifted_by_the_segment_start(monkeypatch):
    segments = [
        _Segment("alpha beta", [" alpha", " beta"], [0.0, 0.5], start=2.0, end=4.0),
        _Segment("gamma", [" gamma"], [0.25], start=30.0, end=31.0),
    ]
    _install(monkeypatch, _FakeModel(segments=segments))
    t = _make()
    t.word_timestamps = True

    result = await _run(t, 40.0)

    assert [(w.word, w.start) for w in result.words] == [("alpha", 2.0), ("beta", 2.5), ("gamma", 30.25)]
    assert len(result.words) == len(result.text.split())


@pytest.mark.asyncio
async def test_long_audio_without_speech_gives_an_empty_transcript(monkeypatch):
    model = _FakeModel(segments=[])
    _install(monkeypatch, model)

    result = await _run(_make(), 30.0)

    assert result.done is True
    assert result.text == ""
    assert result.words == []


@pytest.mark.asyncio
async def test_a_blank_segment_does_not_add_a_stray_space(monkeypatch):
    segments = [_Segment("one", start=0, end=1), _Segment("  ", start=2, end=3), _Segment("two", start=4, end=5)]
    _install(monkeypatch, _FakeModel(segments=segments))

    result = await _run(_make(), 30.0)

    assert result.text == "one two"


@pytest.mark.asyncio
async def test_transcribe_file_runs_a_long_chunk_through_the_vad(monkeypatch):
    """The upload path: 60 s chunks reach the engine as one recording each."""
    import io
    import wave

    import nexusvox.file_transcribe as ft

    model = _FakeModel(segments=[_Segment("spoken", [" spoken"], [0.0], start=5.0, end=7.0)])
    _install(monkeypatch, model)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(_pcm(50.0))

    result = await ft.transcribe_file(
        [buf.getvalue()], InferenceConfig(model="parakeet-tdt-0.6b"), device="cpu", word_timestamps=True
    )

    assert result.text == "spoken"
    assert [(w.word, w.start) for w in result.words] == [("spoken", 5.0)]
    assert model.calls[0]["vad"] is True
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


def _dashboard_client(db, session_factory, monkeypatch, device):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.inference.device = device
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: d if d in ("cuda", "cpu") else "cpu")
    app = _create_app(DashboardAPI(session_factory, config, db=db))
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


def test_parakeet_is_listed_and_switchable_on_cpu(db, session_factory, monkeypatch):
    with _dashboard_client(db, session_factory, monkeypatch, "cpu") as client:
        models = {m["id"]: m for m in client.get("/api/models").get_json()}

    assert models["parakeet-tdt-0.6b"]["inprocess_supported"] is True
    assert models["parakeet-tdt-0.6b"]["requires_gpu"] is False
    assert "voxtral-mini-4b" not in models
