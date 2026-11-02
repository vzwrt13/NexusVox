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
    state = {"up": True, "hang": False, "probes": 0, "clock": 100.0, "posted": 0, "preloads": 0}

    def fake_probe(url: str, timeout: float = 0.5) -> bool:
        state["probes"] += 1
        return state["up"]

    monkeypatch.setattr(auto_parakeet, "probe_server", fake_probe)
    monkeypatch.setattr(auto_parakeet.time, "monotonic", lambda: state["clock"])
    monkeypatch.setattr(
        AutoParakeetTranscriber, "_preload_cpu", lambda self: state.__setitem__("preloads", state["preloads"] + 1)
    )

    async def fake_finish(self, timeout: float = 30.0) -> TranscriptionResult:
        state["posted"] += 1
        if not state["up"]:
            raise RuntimeError("connection refused")
        result = TranscriptionResult()
        if state["hang"]:
            return result  # the real client swallows a timeout and returns nothing
        result.finalize(text="from gpu")
        return result

    monkeypatch.setattr(OpenAIHttpTranscriber, "finish", fake_finish)
    return state


def make() -> tuple[AutoParakeetTranscriber, FakeCpu]:
    cpu = FakeCpu()
    return AutoParakeetTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"), cpu=cpu), cpu


async def cycle(transcriber: AutoParakeetTranscriber) -> str:
    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00\x01" * 160)
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
    assert transcriber.needs_docker is True


async def test_cpu_used_when_server_unreachable(server):
    server["up"] = False
    transcriber, _ = make()
    assert await cycle(transcriber) == "from cpu"
    assert transcriber.engine == CPU
    assert transcriber.model == "cpu-model"
    assert transcriber.supports_diarization is False
    assert "GPU" in transcriber.no_diarization_reason
    assert server["preloads"] == 1  # the CPU model starts loading as soon as it is needed


async def test_cpu_engine_has_no_docker_profile_to_stop(server):
    server["up"] = False
    transcriber, _ = make()
    assert transcriber.needs_docker is False


async def test_server_coming_back_is_used_without_restart(server):
    server["up"] = False
    transcriber, _ = make()
    assert await cycle(transcriber) == "from cpu"
    server["up"] = True
    server["clock"] += auto_parakeet.PROBE_TTL_S + 1
    await transcriber.prepare()  # what the monitor (or a file job) does
    assert await cycle(transcriber) == "from gpu"
    assert transcriber.engine == GPU


async def test_prepare_caches_the_probe_without_a_monitor(server):
    transcriber, _ = make()
    await transcriber.prepare()
    await transcriber.prepare()
    assert server["probes"] == 1
    server["clock"] += auto_parakeet.PROBE_TTL_S + 0.1
    await transcriber.prepare()
    assert server["probes"] == 2


async def test_connect_does_not_wait_for_a_probe_once_one_exists(server):
    transcriber, _ = make()
    await transcriber.prepare()
    transcriber._monitor_started = True  # a monitor keeps the answer current; connect only reads it
    server["up"] = False  # changed, but nobody has noticed yet
    server["clock"] += auto_parakeet.PROBE_TTL_S + 1
    await transcriber.connect()
    assert transcriber._cycle == GPU  # the last answer, no blocking probe on the way


async def test_engine_is_pinned_for_the_whole_cycle(server):
    transcriber, cpu = make()
    await transcriber.connect()
    transcriber._apply(False)  # the monitor flips mid-take
    await transcriber.send_audio_chunk(b"\x00\x01" * 160)
    assert cpu.buffer == b""  # the chunk still went to the engine the take began on
    assert (await transcriber.finish()).text == "from gpu"


async def test_failed_gpu_take_is_replayed_on_the_cpu(server):
    transcriber, cpu = make()
    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00\x01" * 160)
    server["up"] = False  # between the probe and the POST
    result = await transcriber.finish()
    assert result.text == "from cpu"
    assert bytes(cpu.buffer) == b"\x00\x01" * 160  # the audio was not thrown away
    assert transcriber.engine == CPU


async def test_hung_server_is_marked_unreachable(server):
    server["hang"] = True
    transcriber, cpu = make()
    assert await cycle(transcriber) == "from cpu"
    assert transcriber.engine == CPU
    assert len(cpu.buffer) == 320


async def test_warm_up_sends_silent_clip_to_server(server):
    transcriber, cpu = make()
    await transcriber.warm_up()
    assert server["posted"] == 1
    assert cpu.connects == 0


async def test_warm_up_does_not_touch_a_running_dictation(server):
    transcriber, _ = make()
    await transcriber.connect()
    await transcriber.send_audio_chunk(b"\x00\x01" * 160)
    await transcriber.warm_up()
    assert transcriber._gpu.connected is True
    assert len(transcriber._gpu._audio_buffer) == 320
    assert (await transcriber.finish()).text == "from gpu"


async def test_warm_up_failure_never_raises(server, monkeypatch):
    async def boom(self, timeout: float = 30.0):
        raise RuntimeError("model still loading")

    monkeypatch.setattr(OpenAIHttpTranscriber, "finish", boom)
    transcriber, _ = make()
    await transcriber.warm_up()  # logged, not raised
    assert await cycle_after_failed_warm_up(transcriber) is None


async def cycle_after_failed_warm_up(transcriber) -> None:
    assert not transcriber._gpu.connected
    return None


async def test_warm_up_with_the_server_down_starts_the_cpu_model(server):
    server["up"] = False
    transcriber, _ = make()
    await transcriber.warm_up()
    assert server["preloads"] == 1
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


def test_health_url_follows_the_configured_server():
    default = InferenceConfig(model="parakeet-tdt-0.6b")
    assert auto_parakeet.health_url_for(default) == "http://127.0.0.1:8002/health"
    remote = InferenceConfig(model="parakeet-tdt-0.6b", server_url="http://gpu-box.lan:9000/v1/audio/transcriptions")
    assert auto_parakeet.health_url_for(remote) == "http://gpu-box.lan:9000/health"
    secure = InferenceConfig(model="parakeet-tdt-0.6b", server_url="https://gpu.example.org/v1/audio/transcriptions")
    assert auto_parakeet.health_url_for(secure) == "https://gpu.example.org/health"


def test_factory_wraps_only_for_auto_fallback_on_gpu():
    config = InferenceConfig(model="parakeet-tdt-0.6b")
    assert isinstance(create_transcriber(config, device="cuda", auto_fallback=True), AutoParakeetTranscriber)
    assert isinstance(create_transcriber(config, device="cuda"), OpenAIHttpTranscriber)
    assert not isinstance(create_transcriber(config, device="cpu", auto_fallback=True), AutoParakeetTranscriber)
    voxtral = InferenceConfig(model="voxtral-mini-4b")
    assert not isinstance(create_transcriber(voxtral, device="cuda", auto_fallback=True), AutoParakeetTranscriber)
