"""Engine "auto" on a GPU machine: Parakeet on the Docker server when it answers, else on the CPU.

The pair is one transcriber to the rest of the app. The engine is decided by a health probe of the
configured server (`[inference].server_url`, same host and port) with a short timeout:

- A background monitor thread (started by `warm_up`) probes every `PROBE_TTL_S` and flips the engine, so
  a dictation never waits for a probe and a server that comes back is used within seconds, no restart.
- Without a monitor (a bare transcriber, tests) `prepare()` probes when the answer is older than the TTL.
- `connect()` pins the engine for the whole cycle, so the monitor flipping it mid-take cannot split one
  recording across two engines.

A GPU cycle that fails (connection error, server error, a hung server that returned nothing) is marked
unreachable and its audio is replayed through the CPU engine, so the take is not lost.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import weakref
from typing import BinaryIO
from urllib.parse import urlsplit, urlunsplit

from ..config import MODEL_REGISTRY, InferenceConfig
from ..transcriber import (
    BaseTranscriber,
    OpenAIHttpTranscriber,
    SpeakerTurn,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

# The health check must never hold up anything for long: a server that is up answers in milliseconds.
PROBE_TIMEOUT_S = 0.5
# How often the monitor probes, and how long one answer is trusted when no monitor runs.
PROBE_TTL_S = 3.0
# The warm-up clip: half a second of silence, enough to make the server load and compile its kernels.
_WARM_UP_SILENCE_S = 0.5
_WARM_UP_TIMEOUT_S = 90.0

GPU = "gpu"
CPU = "cpu"


def health_url_for(config: InferenceConfig) -> str:
    """The server's health URL: the registry's path on the host and port of `[inference].server_url`.

    `localhost` becomes 127.0.0.1: on Windows it also tries ::1 first, which a refused connection makes slow.
    """
    registry_url = str(MODEL_REGISTRY[config.model].get("health_url") or "")
    if not registry_url:
        return ""
    server = urlsplit(config.server_url)
    host = server.hostname
    if not host:
        return registry_url
    if host == "localhost":
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{server.port}" if server.port else host
    scheme = "https" if server.scheme in ("https", "wss") else "http"
    return urlunsplit((scheme, netloc, urlsplit(registry_url).path or "/health", "", ""))


def probe_server(health_url: str, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Whether the server's health endpoint answers 2xx within `timeout`. Never raises."""
    if not health_url:
        return False
    try:
        import httpx

        return httpx.get(health_url, timeout=timeout).is_success
    except Exception:
        return False


def _monitor(ref: weakref.ReferenceType[AutoParakeetTranscriber], health_url: str) -> None:
    """Probe until the transcriber is gone (weak reference, so a switched-away model ends its monitor)."""
    while True:
        time.sleep(PROBE_TTL_S)
        owner = ref()
        if owner is None:
            return
        owner._apply(probe_server(health_url))
        del owner


class AutoParakeetTranscriber(BaseTranscriber):
    """Parakeet on the GPU server while it is reachable, in-process on the CPU otherwise."""

    supports_word_timestamps = True
    warms_up_at_start = True

    def __init__(self, config: InferenceConfig, cpu: BaseTranscriber | None = None) -> None:
        self._health_url = health_url_for(config)
        self._config = config
        self._gpu: BaseTranscriber = OpenAIHttpTranscriber(config)
        self._cpu = cpu
        self._engine = GPU  # until the first probe says otherwise
        self._last_logged: str | None = None
        self._probed_at: float | None = None  # None: never probed
        self._word_timestamps = False
        self._monitor_started = False
        self._lock = threading.Lock()
        # The running cycle: the engine pinned by connect() and the audio, kept for a replay on the CPU.
        self._cycle = GPU
        self._pcm = bytearray()
        self.model = self._gpu.model

    # ---- Engine choice ------------------------------------------------------

    @property
    def engine(self) -> str:
        """`gpu` or `cpu`: what the next cycle uses."""
        return self._current_engine()

    def _current_engine(self) -> str:
        if self._probed_at is None:
            self._apply(probe_server(self._health_url))
        return self._engine

    def _stale(self) -> bool:
        return self._probed_at is None or time.monotonic() - self._probed_at >= PROBE_TTL_S

    def _apply(self, reachable: bool) -> None:
        """Take a probe answer: switch the engine, log a change, load the CPU model when it is now needed."""
        with self._lock:
            self._probed_at = time.monotonic()
            self._engine = GPU if reachable else CPU
            changed = self._engine != self._last_logged
            self._last_logged = self._engine
        if not changed:
            return
        if self._engine == GPU:
            logger.info("Auto engine: Parakeet server reachable, transcribing on the GPU")
        else:
            logger.info("Auto engine: Parakeet server not reachable, transcribing on the CPU (no speakers)")
            self._preload_cpu()

    def _preload_cpu(self) -> None:
        """Start loading (or downloading) the CPU model in the background, so a take on it is not lost to a load."""

        def run() -> None:
            try:
                cpu = self._cpu_transcriber()
                asyncio.run(cpu.connect())  # begins the model load in its own thread and returns
                asyncio.run(cpu.disconnect())
            except Exception:
                logger.warning("Preloading the CPU Parakeet model failed", exc_info=True)

        threading.Thread(target=run, name="auto-cpu-preload", daemon=True).start()

    def _cpu_transcriber(self) -> BaseTranscriber:
        with self._lock:
            if self._cpu is None:
                from .local_parakeet import LocalParakeetTranscriber

                self._cpu = LocalParakeetTranscriber(self._config)
                self._cpu.word_timestamps = self._word_timestamps
            return self._cpu

    def _start_monitor(self) -> None:
        if self._monitor_started:
            return
        self._monitor_started = True
        threading.Thread(
            target=_monitor, args=(weakref.ref(self), self._health_url), name="auto-engine-monitor", daemon=True
        ).start()

    async def prepare(self) -> None:
        """Settle the engine for what follows, so the flags below describe it.

        With a monitor running its answer is current; otherwise an answer older than the TTL is refreshed.
        """
        if self._probed_at is None or (not self._monitor_started and self._stale()):
            reachable = await asyncio.get_running_loop().run_in_executor(None, probe_server, self._health_url)
            self._apply(reachable)
        self.model = self._gpu.model if self._engine == GPU else self._cpu_transcriber().model

    # ---- Properties that follow the engine ----------------------------------

    @property
    def needs_docker(self) -> bool:  # type: ignore[override]
        """Only the GPU engine has a Docker profile to stop on a model switch."""
        return self._current_engine() == GPU

    @property
    def word_timestamps(self) -> bool:  # type: ignore[override]
        return self._word_timestamps

    @word_timestamps.setter
    def word_timestamps(self, value: bool) -> None:
        self._word_timestamps = value
        self._gpu.word_timestamps = value
        if self._cpu is not None:
            self._cpu.word_timestamps = value

    @property
    def supports_diarization(self) -> bool:  # type: ignore[override]
        return self._current_engine() == GPU

    @property
    def no_diarization_reason(self) -> str:  # type: ignore[override]
        return "" if self._current_engine() == GPU else self._cpu_transcriber().no_diarization_reason

    def load_status(self) -> dict[str, str | None] | None:
        return self._cpu.load_status() if self._engine == CPU and self._cpu is not None else None

    # ---- Cycle --------------------------------------------------------------

    @property
    def _cycle_transcriber(self) -> BaseTranscriber:
        return self._gpu if self._cycle == GPU else self._cpu_transcriber()

    async def connect(self) -> None:
        if self._probed_at is None:
            await self.prepare()
        elif self._stale() and not self._monitor_started:
            # No monitor: refresh in the background, this cycle goes with the last answer.
            threading.Thread(
                target=lambda: self._apply(probe_server(self._health_url)), name="auto-engine-probe", daemon=True
            ).start()
        self._cycle = self._engine
        self._pcm = bytearray()
        self.model = self._gpu.model if self._cycle == GPU else self._cpu_transcriber().model
        await self._cycle_transcriber.connect()

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        if self._cycle == GPU:
            self._pcm.extend(pcm16_bytes)
        await self._cycle_transcriber.send_audio_chunk(pcm16_bytes)

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        if self._cycle != GPU:
            return await self._cpu_transcriber().finish(timeout=timeout)
        pcm = bytes(self._pcm)
        try:
            result = await self._gpu.finish(timeout=timeout)
            if result.done:
                return result
            # The client swallows a timeout and returns nothing: a hung server counts as unreachable.
            logger.warning("Parakeet server returned no result; replaying the take on the CPU")
        except Exception:
            logger.warning("Parakeet server failed; replaying the take on the CPU", exc_info=True)
        self._apply(False)
        cpu = self._cpu_transcriber()
        await cpu.connect()
        await cpu.send_audio_chunk(pcm)
        return await cpu.finish(timeout=timeout)

    async def disconnect(self) -> None:
        self._pcm = bytearray()
        await self._gpu.disconnect()
        if self._cpu is not None:
            await self._cpu.disconnect()

    @property
    def connected(self) -> bool:
        return self._cycle_transcriber.connected

    async def diarize(self, wav: bytes | BinaryIO, timeout: float) -> list[SpeakerTurn]:
        if self._current_engine() != GPU:
            raise NotImplementedError("Speaker detection needs the GPU server")
        return await self._gpu.diarize(wav, timeout)

    # ---- Start-up warm-up ---------------------------------------------------

    async def warm_up(self) -> None:
        """Make the first dictation fast: start the monitor, then one silent clip through the server, or the CPU
        model loading.

        The clip goes through a client of its own, so a dictation during the (cold, ~17 s) warm-up keeps its
        buffer. Never raises; the app runs it as a background task.
        """
        try:
            await self.prepare()
            self._start_monitor()
            if self._engine == GPU:
                warm = OpenAIHttpTranscriber(self._config)
                await warm.connect()
                await warm.send_audio_chunk(bytes(int(16_000 * _WARM_UP_SILENCE_S) * 2))
                await warm.finish(timeout=_WARM_UP_TIMEOUT_S)
                logger.info("Parakeet server warmed up")
            # On the CPU `_apply` already started the model load.
        except Exception:
            logger.warning("Warm-up of the %s engine failed; first dictation may be slow", self._engine, exc_info=True)
