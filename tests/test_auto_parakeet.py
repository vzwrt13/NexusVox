"""Engine "auto" on a GPU machine: Parakeet server while reachable, CPU Parakeet otherwise."""

from __future__ import annotations

import pytest

from nexusvox.config import InferenceConfig
from nexusvox.transcriber import (
    BaseTranscriber,
    OpenAIHttpTranscriber,
    TranscriptionResult,
    create_transcriber,
)
from nexusvox.transcribers import auto_parakeet
from nexusvox.transcribers.auto_parakeet import CPU, GPU, AutoParakeetTranscriber


class FakeCpu(BaseTranscriber):
    needs_docker = False
    no_diarization_reason = "Speaker detection needs the GPU container"

    def __init__(self) -> None:
        self.model = "cpu-model"
        self.buffer = bytearray()
        self.is_connected = False
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1
        self.is_connected = True

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        self.buffer.extend(pcm16_bytes)

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        result = TranscriptionResult()
        result.finalize(text="from cpu")
        return result

    async def disconnect(self) -> None:
        self.is_connected = False

    @property
    def connected(self) -> bool:
        return self.is_connected


@pytest.fixture
def server(monkeypatch):
    """A switchable Parakeet server: `state["up"]` decides what the health probe sees."""
    state = {"up": True, "probes": 0, "clock": 100.0, "posted": 0}

    def fake_probe(url: str, timeout: float = 0.5) -> bool:
        state["probes"] += 1
        return state["up"]

    monkeypatch.setattr(auto_parakeet, "probe_server", fake_probe)
    monkeypatch.setattr(auto_parakeet.time, "monotonic", lambda: state["clock"])

    async def fake_finish(self, timeout: float = 30.0) -> TranscriptionResult:
        state["posted"] += 1
        if not state["up"]:
            raise RuntimeError("connection refused")
        result = TranscriptionResult()
        result.finalize(text="from gpu")
        return result

    monkeypatch.setattr(OpenAIHttpTranscriber, "finish", fake_finish)
    return state


def make() -> tuple[AutoParakeetTranscriber, FakeCpu]:
    cpu = FakeCpu()
    return AutoParakeetTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"), cpu=cpu), cpu


async def cycle(transcriber: AutoParakeetTranscriber) -> str:
    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00\x00" * 160)
    result = await transcriber.finish()
    await transcriber.disconnect()
    return result.text


async def test_gpu_used_when_server_reachable(server):
    transcriber, cpu = make()
    assert await cycle(transcriber) == "from gpu"
    assert transcriber.engine == GPU
    assert cpu.connects == 0
    assert transcriber.supports_diarization is True
    assert transcriber.no_diarization_reason == ""


async def test_cpu_used_when_server_unreachable(server):
    server["up"] = False
    transcriber, cpu = make()
    assert await cycle(transcriber) == "from cpu"
    assert transcriber.engine == CPU
    assert transcriber.model == "cpu-model"
    assert transcriber.supports_diarization is False
    assert "GPU" in transcriber.no_diarization_reason


async def test_server_coming_back_is_used_without_restart(server):
    server["up"] = False
    transcriber, _ = make()
    assert await cycle(transcriber) == "from cpu"
    server["up"] = True
    server["clock"] += auto_parakeet.PROBE_TTL_S + 1
    assert await cycle(transcriber) == "from gpu"
    assert transcriber.engine == GPU


async def test_probe_is_cached_between_cycles(server):
    transcriber, _ = make()
    await cycle(transcriber)
    await cycle(transcriber)
    assert server["probes"] == 1
    server["clock"] += auto_parakeet.PROBE_TTL_S + 0.1
    await cycle(transcriber)
    assert server["probes"] == 2


async def test_failed_gpu_cycle_reprobes_next_time(server):
    transcriber, _ = make()
    await transcriber.connect()
    server["up"] = False
    with pytest.raises(RuntimeError):
        await transcriber.finish()
    await transcriber.disconnect()
    assert await cycle(transcriber) == "from cpu"


async def test_warm_up_sends_silent_clip_to_server(server):
    transcriber, cpu = make()
    await transcriber.warm_up()
    assert server["posted"] == 1
    assert cpu.connects == 0


async def test_warm_up_failure_never_raises(server):
    # Reachable at the probe, but the clip fails: the app must go on.
    async def boom(self, timeout: float = 30.0):
        raise RuntimeError("model still loading")

    transcriber, _ = make()
    transcriber._gpu.finish = boom.__get__(transcriber._gpu)
    await transcriber.warm_up()
    assert await cycle_after_failed_warm_up(transcriber) is None


async def cycle_after_failed_warm_up(transcriber) -> None:
    assert not transcriber._gpu.connected
    return None


async def test_warm_up_loads_cpu_model_when_server_down(server):
    server["up"] = False
    transcriber, cpu = make()
    await transcriber.warm_up()
    assert cpu.connects == 1
    assert server["posted"] == 0


async def test_diarize_refused_on_cpu(server):
    server["up"] = False
    transcriber, _ = make()
    await transcriber.prepare()
    with pytest.raises(NotImplementedError):
        await transcriber.diarize(b"", 1.0)


def test_word_timestamps_follow_to_both_engines():
    transcriber, cpu = make()
    transcriber.word_timestamps = True
    assert transcriber._gpu.word_timestamps is True
    assert cpu.word_timestamps is True


def test_probe_server_false_without_url_or_on_error():
    assert auto_parakeet.probe_server("") is False
    assert auto_parakeet.probe_server("http://127.0.0.1:9/health", timeout=0.2) is False


def test_factory_wraps_only_for_auto_fallback_on_gpu():
    config = InferenceConfig(model="parakeet-tdt-0.6b")
    assert isinstance(create_transcriber(config, device="cuda", auto_fallback=True), AutoParakeetTranscriber)
    assert isinstance(create_transcriber(config, device="cuda"), OpenAIHttpTranscriber)
    assert not isinstance(create_transcriber(config, device="cpu", auto_fallback=True), AutoParakeetTranscriber)
    voxtral = InferenceConfig(model="voxtral-mini-4b")
    assert not isinstance(create_transcriber(voxtral, device="cuda", auto_fallback=True), AutoParakeetTranscriber)
