"""Microphone audio capture at 16kHz mono PCM16."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Callable

import numpy as np
import sounddevice as sd

from . import audio_state
from .config import AudioConfig

logger = logging.getLogger(__name__)


class AudioCapture:
    """Captures microphone audio and yields PCM16 chunks for streaming."""

    def __init__(self, config: AudioConfig) -> None:
        self._sample_rate = config.sample_rate
        self._chunk_size = config.chunk_size
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._chunk_count = 0
        self._log_interval = 0  # set in start()
        # Called from the audio thread with each chunk's RMS (0..1); must return at once.
        self.on_rms: Callable[[float], None] | None = None
        # Asked per chunk before the RMS is computed for `on_rms`; None means always.
        self.rms_wanted: Callable[[], bool] | None = None

    def _audio_callback(
        self,
        indata: memoryview,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags,
    ) -> None:
        """Called by sounddevice from audio thread — push raw bytes to queue."""
        if status:
            logger.warning("Audio callback status: %s", status)
        # indata is float32 numpy array; convert to int16 PCM bytes
        samples = indata[:, 0]
        clipped = np.clip(samples, -1.0, 1.0)
        pcm_bytes = (clipped * 32767).astype(np.int16).tobytes()

        self._chunk_count += 1
        log_now = self._log_interval > 0 and self._chunk_count % self._log_interval == 0
        feed = self.on_rms is not None and (self.rms_wanted is None or self.rms_wanted())
        if feed or log_now:
            rms = float(np.sqrt(np.mean(samples**2)))
            if feed:
                self.on_rms(rms)
        if log_now:
            db = 20 * math.log10(max(rms, 1e-10))
            logger.info("Audio: chunks=%d rms=%.1fdB", self._chunk_count, db)

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, pcm_bytes)

    def start(self) -> None:
        """Start capturing audio from the default microphone."""
        self._loop = asyncio.get_event_loop()
        self._chunk_count = 0
        # Calculate frames per callback to produce ~chunk_size bytes
        # Each PCM16 sample = 2 bytes, mono = 1 channel
        frames_per_chunk = self._chunk_size // 2
        # Log audio level roughly every second
        self._log_interval = max(1, self._sample_rate // frames_per_chunk)

        # Log selected device
        try:
            dev = sd.query_devices(kind="input")
            logger.info("Audio device: %s, %d Hz", dev["name"], self._sample_rate)
        except Exception:
            logger.warning("Could not query input device")

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            blocksize=frames_per_chunk,
            callback=self._audio_callback,
        )
        self._stream.start()
        audio_state.stream_opened()

    def stop(self) -> None:
        """Stop capturing audio."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            audio_state.stream_closed()
        # Signal end of stream
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield PCM16 audio chunks until stopped."""
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk
