"""Engine "auto" on a GPU machine: Parakeet on the Docker server when it answers, else on the CPU.

The pair is one transcriber to the rest of the app. Each cycle (`connect()`) asks the server's health
endpoint with a short timeout and uses the GPU client when it answers, the in-process CPU Parakeet when
it does not. A server that comes back is used again from the next cycle on; nothing is restarted.
The answer is cached for a few seconds so back-to-back cycles and the many chunks of an upload cost
one probe, and a refused connection to localhost returns at once, so a dictation is not delayed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import BinaryIO

from ..config import MODEL_REGISTRY, InferenceConfig
from ..transcriber import (
    BaseTranscriber,
    OpenAIHttpTranscriber,
    SpeakerTurn,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

# The health check must never hold up a dictation: a server that is up answers in milliseconds.
PROBE_TIMEOUT_S = 0.5
# How long one probe result is trusted. Short, so a server that comes up is used within seconds.
PROBE_TTL_S = 3.0
# The warm-up clip: half a second of silence, enough to make the server load and compile its kernels.
_WARM_UP_SILENCE_S = 0.5
_WARM_UP_TIMEOUT_S = 90.0

GPU = "gpu"
CPU = "cpu"


def probe_server(health_url: str, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Whether the server's health endpoint answers 2xx within `timeout`. Never raises."""
    if not health_url:
        return False
    try:
        import httpx

        return httpx.get(health_url, timeout=timeout).is_success
    except Exception:
        return False


class AutoParakeetTranscriber(BaseTranscriber):
    """Parakeet on the GPU server while it is reachable, in-process on the CPU otherwise."""

    # The Docker profile is stopped on a model switch, as for the plain server client.
    needs_docker = True
    supports_word_timestamps = True

    def __init__(self, config: InferenceConfig, cpu: BaseTranscriber | None = None) -> None:
        self._health_url = str(MODEL_REGISTRY[config.model].get("health_url") or "")
        self._config = config
        self._gpu: BaseTranscriber = OpenAIHttpTranscriber(config)
        self._cpu = cpu
        self._engine = GPU  # until the first probe says otherwise
        self._last_logged: str | None = None
        self._probed_at = float("-inf")
        self._word_timestamps = False
        self.model = self._gpu.model

    # ---- Engine choice ------------------------------------------------------

    @property
    def engine(self) -> str:
        """`gpu` or `cpu`: what the next cycle (or the running one) uses."""
        return self._engine

    @property
    def _active(self) -> BaseTranscriber:
        return self._gpu if self._engine == GPU else self._cpu_transcriber()

    def _cpu_transcriber(self) -> BaseTranscriber:
        if self._cpu is None:
            from .local_parakeet import LocalParakeetTranscriber

            self._cpu = LocalParakeetTranscriber(self._config)
            self._cpu.word_timestamps = self._word_timestamps
        return self._cpu

    async def prepare(self) -> None:
        """Pick the engine for what follows (cached for `PROBE_TTL_S`), so the flags below describe it."""
        now = time.monotonic()
        if now - self._probed_at >= PROBE_TTL_S:
            reachable = await asyncio.get_running_loop().run_in_executor(None, probe_server, self._health_url)
            self._probed_at = time.monotonic()
            self._engine = GPU if reachable else CPU
        if self._engine != self._last_logged:
            if self._engine == GPU:
                logger.info("Auto engine: Parakeet server reachable, transcribing on the GPU")
            else:
                logger.info("Auto engine: Parakeet server not reachable, transcribing on the CPU (no speakers)")
            self._last_logged = self._engine
        self.model = self._active.model

    def _mark_unreachable(self) -> None:
        """A cycle on the GPU failed: the next one must probe again and may use the CPU."""
        self._probed_at = float("-inf")

    # ---- Properties that follow the engine ----------------------------------

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
        return self._engine == GPU

    @property
    def no_diarization_reason(self) -> str:  # type: ignore[override]
        return "" if self._engine == GPU else self._cpu_transcriber().no_diarization_reason

    def load_status(self) -> dict[str, str | None] | None:
        return self._cpu.load_status() if self._engine == CPU and self._cpu is not None else None

    # ---- Cycle --------------------------------------------------------------

    async def connect(self) -> None:
        await self.prepare()
        await self._active.connect()

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        await self._active.send_audio_chunk(pcm16_bytes)

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        engine = self._engine
        try:
            return await self._active.finish(timeout=timeout)
        except Exception:
            if engine == GPU:
                self._mark_unreachable()
            raise

    async def disconnect(self) -> None:
        await self._gpu.disconnect()
        if self._cpu is not None:
            await self._cpu.disconnect()

    @property
    def connected(self) -> bool:
        return self._active.connected

    async def diarize(self, wav: bytes | BinaryIO, timeout: float) -> list[SpeakerTurn]:
        if self._engine != GPU:
            raise NotImplementedError("Speaker detection needs the GPU server")
        return await self._gpu.diarize(wav, timeout)

    # ---- Start-up warm-up ---------------------------------------------------

    async def warm_up(self) -> None:
        """Make the first dictation fast: one silent clip through the server, or the CPU model loaded.

        Never raises; the app runs it as a background task.
        """
        try:
            await self.prepare()
            if self._engine == GPU:
                silence = bytes(int(16_000 * _WARM_UP_SILENCE_S) * 2)
                await self._gpu.connect()
                await self._gpu.send_audio_chunk(silence)
                await self._gpu.finish(timeout=_WARM_UP_TIMEOUT_S)
                logger.info("Parakeet server warmed up")
            else:
                cpu = self._cpu_transcriber()
                await cpu.connect()
                await cpu.disconnect()
                logger.info("CPU Parakeet preload started")
        except Exception:
            logger.warning(
                "Warm-up of the %s engine failed; the first dictation may be slow", self._engine, exc_info=True
            )
        finally:
            await self._gpu.disconnect()
