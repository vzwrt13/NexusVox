"""Microphone audio capture at 16kHz mono PCM16."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Callable

import numpy as np
import sounddevice as sd

from ..config import AudioConfig
from . import state as audio_state
from .devices import find_input_device, input_open_lock, wasapi_index

logger = logging.getLogger("nexusvox.audio")

# A chunk quieter than this (RMS of full scale) counts as silent: a muted or unplugged microphone reads far below it,
# room noise of a working one stays above it.
SILENT_DB = -60.0
# A recording shorter than this is a tap, not evidence of a muted microphone: no silence warning for it.
MIN_SILENCE_WARNING_S = 0.5
# A muted or blocked microphone often sends one click when the stream opens, so "silent throughout" allows for it.
SILENT_WARNING_SHARE = 0.95


def _to_db(level: float) -> float:
    return 20 * math.log10(max(level, 1e-10))


class RecordingLevels:
    """Level statistics of one recording, filled chunk by chunk from the audio thread."""

    def __init__(self) -> None:
        self.chunks = 0
        self.frames = 0  # samples actually delivered, whatever block size the host API used
        self.silent = 0
        self.peak = 0.0  # highest chunk RMS, 0..1
        self._power = 0.0  # sum of the chunks' mean squares

    def add(self, rms: float, frames: int) -> None:
        self.chunks += 1
        self.frames += frames
        self._power += rms * rms
        self.peak = max(self.peak, rms)
        if _to_db(rms) < SILENT_DB:
            self.silent += 1

    @property
    def average_db(self) -> float:
        return _to_db(math.sqrt(self._power / self.chunks)) if self.chunks else _to_db(0.0)

    @property
    def peak_db(self) -> float:
        return _to_db(self.peak)

    @property
    def silent_share(self) -> float:
        return self.silent / self.chunks if self.chunks else 0.0


class AudioCapture:
    """Captures microphone audio and yields PCM16 chunks for streaming."""

    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self._sample_rate = config.sample_rate
        self._chunk_size = config.chunk_size
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._log_interval = 0  # set in start()
        self._levels = RecordingLevels()
        self._device_name = ""  # the microphone of the running recording
        # Called from the audio thread with each chunk's RMS (0..1); must return at once.
        self.on_rms: Callable[[float], None] | None = None
        # Asked per chunk before the RMS is passed to `on_rms`; None means always.
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

        # dot product, not samples**2: no temporary array on the real-time audio thread
        rms = math.sqrt(float(np.dot(samples, samples)) / max(1, len(samples)))
        levels = self._levels
        levels.add(rms, len(samples))
        if self.on_rms is not None and (self.rms_wanted is None or self.rms_wanted()):
            self.on_rms(rms)
        if self._log_interval > 0 and levels.chunks % self._log_interval == 0:
            logger.debug("Audio: chunks=%d rms=%.1fdB", levels.chunks, _to_db(rms))

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, pcm_bytes)

    def start(self) -> None:
        """Start capturing audio from the microphone chosen in `[audio].device`, else the default one."""
        self._loop = asyncio.get_event_loop()
        # A stream that is still open (a stop that failed) would keep feeding the new recording's queue and levels.
        try:
            self._close_stream()
        except Exception:
            logger.debug("Closing the stream of the earlier recording failed", exc_info=True)
        # A recording starts with an empty queue: chunks or an end marker an earlier one left behind
        # (a stream that stopped reading early) would otherwise come out as this recording's audio.
        self._queue = asyncio.Queue()
        self._levels = RecordingLevels()
        # Calculate frames per callback to produce ~chunk_size bytes
        # Each PCM16 sample = 2 bytes, mono = 1 channel
        frames_per_chunk = self._chunk_size // 2
        # Log audio level roughly every second (at DEBUG)
        self._log_interval = max(1, self._sample_rate // frames_per_chunk)

        # Finding the device and opening the stream happen under one lock, and the stream is counted before it is
        # let go: no device refresh can restart PortAudio in between (audio/devices.py).
        with input_open_lock:
            self._stream = self._open_stream(frames_per_chunk)
            audio_state.stream_opened()

    def _choices(self) -> list[tuple[int | None, dict, str]]:
        """The microphones to try, in order: the one chosen in `[audio].device` (read now, not at start-up: the
        dashboard changes it while the app runs), then the system default input, which always worked before."""
        choices: list[tuple[int | None, dict, str]] = []
        if self._config.device:
            try:
                dev = find_input_device(sd, self._config.device)
                extra: dict = {}
                if dev["hostapi"] == wasapi_index(sd):
                    # WASAPI shared mode only opens at the device's own rate unless Windows converts to ours.
                    extra["extra_settings"] = sd.WasapiSettings(auto_convert=True)
                choices.append((int(dev["index"]), extra, dev["name"]))
            except Exception:
                logger.warning("Could not prepare the chosen microphone %r; using the default", self._config.device)
        try:
            default_name = sd.query_devices(kind="input")["name"]
        except Exception:
            logger.warning("Could not query input device")
            default_name = "default input"
        choices.append((None, {}, default_name))
        return choices

    def _open_stream(self, frames_per_chunk: int):
        """The started input stream on the first microphone that opens."""
        error: Exception | None = None
        for device, extra, name in self._choices():
            stream = None
            try:
                stream = sd.InputStream(
                    device=device,
                    **extra,
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=frames_per_chunk,
                    callback=self._audio_callback,
                )
                stream.start()
            except Exception as exc:
                error = exc
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        logger.debug("Closing the stream that did not start failed", exc_info=True)
                if device is not None:
                    logger.warning("The chosen microphone %r could not be opened (%s); using the default", name, exc)
                continue
            logger.info("Audio device: %s, %d Hz", name, self._sample_rate)
            self._device_name = name
            return stream
        raise error if error is not None else OSError("No microphone could be opened")

    def stop(self) -> None:
        """Stop capturing audio and queue the end marker.

        A device that fails to close (a microphone unplugged at key-up) is logged, not raised: the audio captured
        so far is in the queue, and the end marker after it lets the take be transcribed.
        """
        try:
            if self._stream is not None:
                try:
                    self._close_stream()
                except Exception:
                    logger.warning("Closing the microphone failed; keeping the recording", exc_info=True)
                # Also when the device failed: that is the recording the summary is there to explain.
                self._log_summary()
        finally:
            # Signal end of stream
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def _close_stream(self) -> None:
        """Stop and close the running stream, if any; it is closed and uncounted even when stopping raises."""
        stream, self._stream = self._stream, None
        if stream is not None:
            audio_state.close_stream(stream)

    def _log_summary(self) -> None:
        """One INFO line for the recording that just ended, and a WARNING when it was silent throughout."""
        levels = self._levels
        if levels.chunks == 0:
            return
        seconds = levels.frames / self._sample_rate
        logger.info(
            "Audio: %.1f s, %d chunks, rms avg %.0f dB, peak %.0f dB, %.0f%% silent",
            seconds,
            levels.chunks,
            levels.average_db,
            levels.peak_db,
            levels.silent_share * 100,
        )
        if levels.silent_share >= SILENT_WARNING_SHARE and seconds >= MIN_SILENCE_WARNING_S:
            logger.warning(
                "Audio: the recording was silent (device: %s); the microphone may be muted or the wrong one",
                self._device_name or "unknown",
            )

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield PCM16 audio chunks until stopped."""
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk
