"""A short-lived input level reading for the first-run setup's microphone step.

The dashboard polls `read(device)`; the stream opens on the first poll and closes by itself a few seconds after the
last one (or at once on `close()`), so the microphone is never left open when the page goes away.
"""

from __future__ import annotations

import logging
import math
import threading

import numpy as np

from . import state as audio_state
from .devices import find_input_device, input_open_lock

logger = logging.getLogger(__name__)

# How long the stream stays open after the last poll.
IDLE_CLOSE_S = 3.0
# The scale of the meter: this many dB below full scale is an empty bar.
FLOOR_DB = 60.0


def level_from_rms(rms: float) -> float:
    """0..1 on a dB scale: 0 at -60 dBFS and below, 1 at full scale."""
    if rms <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 + 20.0 * math.log10(rms) / FLOOR_DB))


class LevelMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream = None
        self._device_name: str | None = None
        self._timer: threading.Timer | None = None
        # Which poll armed the idle timer: a timer that has already fired and waits for the lock must not close
        # a stream a newer poll has just renewed.
        self._generation = 0
        self._peak = 0.0

    def _callback(self, indata, frames, time_info, status) -> None:
        rms = float(np.sqrt(np.mean(indata[:, 0] ** 2)))
        if rms > self._peak:
            self._peak = rms

    def _open(self, name: str) -> None:
        import sounddevice as sd

        with input_open_lock:  # finding the device, opening and counting the stream: no device refresh in between
            dev = find_input_device(sd, name)
            stream = sd.InputStream(
                device=int(dev["index"]),
                samplerate=int(dev["default_samplerate"]),
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            try:
                stream.start()
            except Exception:
                stream.close()  # opened but not started: self._stream is still None, so nothing else would close it
                raise
            audio_state.stream_opened()
            self._stream = stream
            self._device_name = name

    def _close_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        stream, self._stream = self._stream, None
        self._device_name = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Closing the level meter failed")
            audio_state.stream_closed()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_if_idle(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._close_locked()

    def read(self, name: str) -> dict:
        """The loudest level since the last read, as `{"level": 0..1}` or `{"level": 0, "error": text}`."""
        with self._lock:
            try:
                if self._stream is None or self._device_name != name:
                    self._close_locked()
                    self._peak = 0.0
                    self._open(name)
            except Exception as exc:
                logger.warning("The level meter could not open %r: %s", name or "the default microphone", exc)
                self._close_locked()
                return {"level": 0.0, "error": "This microphone could not be opened."}
            if self._timer is not None:
                self._timer.cancel()
            self._generation += 1
            self._timer = threading.Timer(IDLE_CLOSE_S, self._close_if_idle, args=(self._generation,))
            self._timer.daemon = True
            self._timer.start()
            peak, self._peak = self._peak, 0.0
        return {"level": round(level_from_rms(peak), 3)}
