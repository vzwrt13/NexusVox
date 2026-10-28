"""A rough transcript of a meeting while it records, shown in the dashboard with Me / Others labels.

The recorder hands every audio block of both tracks to `LiveTranscriber.feed`. A worker thread collects each track
into windows of about `WINDOW_S` seconds, cut at the quietest moment of the window's last two seconds so a word is
rarely split, and sends one window at a time through the transcription callback. Silent windows are not sent, a
track the worker cannot keep up with drops its oldest audio, and the tap never blocks the audio thread.

This transcript is only a preview: nothing is stored, the files on disk are untouched, and the job that runs after
Stop still produces the stored transcript. Live text has no echo filter, no speaker detection and no dictation
filter, so the microphone may repeat what the speakers play.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from .meeting_recorder import AudioFormat
from .meeting_transcribe import _resample, _wav_bytes

logger = logging.getLogger(__name__)

WINDOW_S = 6.0  # audio sent per request; with the request time this is the delay of the live text
CUT_SEARCH_S = 2.0  # the window is cut at the quietest block within its last seconds
BLOCK_S = 0.05
MAX_BACKLOG_S = 30.0  # a track further behind than this drops its oldest audio instead of falling behind for good
MAX_QUEUED_BLOCKS = 2000
SILENCE_RMS = 100.0  # a window whose loudest 50 ms block stays below this is not sent
IDLE_WAIT_S = 0.25

LABELS = {"mic": "Me", "system": "Others"}


def _downmix(data: bytes, channels: int) -> np.ndarray:
    samples = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2")
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
    return samples.astype(np.int16)


def _block_rms(samples: np.ndarray, block: int) -> np.ndarray:
    blocks = len(samples) // block
    if blocks == 0:
        return np.zeros(0)
    view = samples[: blocks * block].astype(np.float32).reshape(blocks, block)
    return np.sqrt((view**2).mean(axis=1))


class _Pending:
    """The audio of one track that has not been sent yet (mono int16 at the device rate)."""

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.chunks: list[np.ndarray] = []
        self.frames = 0
        self.at_s = 0.0  # time of the first pending sample, seconds into the meeting

    def add(self, samples: np.ndarray, end_s: float) -> None:
        if self.frames == 0:
            self.at_s = max(0.0, end_s - len(samples) / self.rate)
        self.chunks.append(samples)
        self.frames += len(samples)

    def take(self, frames: int) -> np.ndarray:
        data = np.concatenate(self.chunks) if len(self.chunks) > 1 else self.chunks[0]
        head, rest = data[:frames], data[frames:]
        self.chunks = [rest] if len(rest) else []
        self.frames = len(rest)
        self.at_s += frames / self.rate
        return head


class LiveTranscriber:
    """Collects both tracks and transcribes them window by window on one worker thread.

    `transcribe(wav_bytes)` is blocking and returns the text of 16 kHz mono PCM16 WAV bytes; it may raise.
    """

    def __init__(
        self,
        transcribe: Callable[[bytes], str],
        *,
        clock: Callable[[], float] = time.monotonic,
        window_s: float = WINDOW_S,
    ) -> None:
        self._transcribe = transcribe
        self._clock = clock
        self._window_s = window_s
        self._queue: queue.Queue[tuple[str, AudioFormat, bytes, float]] = queue.Queue(MAX_QUEUED_BLOCKS)
        self._pending: dict[str, _Pending] = {}
        self._segments: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="meeting-live", daemon=True)
        self._t0 = 0.0
        self._busy_s = 0.0
        self._windows = 0
        self._skipped_s = 0.0
        self._error: str | None = None

    @property
    def active(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def start(self) -> None:
        self._t0 = self._clock()
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """End the worker; the window being transcribed finishes (at most `timeout` seconds are waited)."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def feed(self, name: str, fmt: AudioFormat, data: bytes) -> None:
        """Audio-thread entry: never blocks, a full queue drops the block."""
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait((name, fmt, data, self._clock() - self._t0))
        except queue.Full:
            with self._lock:
                self._skipped_s += len(data) / 2 / max(1, fmt.channels) / max(1, fmt.sample_rate)

    def segments_since(self, after: int = 0) -> list[dict]:
        with self._lock:
            return list(self._segments[max(0, after) :])

    def status(self) -> dict:
        """What the dashboard needs besides the text: whether it runs, how loaded the model is, what failed."""
        elapsed = max(self._clock() - self._t0, 1e-6) if self._t0 else 0.0
        with self._lock:
            return {
                "active": self.active,
                "windows": self._windows,
                "load": round(min(1.0, self._busy_s / elapsed), 3) if elapsed else 0.0,
                "skipped_s": round(self._skipped_s, 1),
                "error": self._error,
                "count": len(self._segments),
            }

    # ---- worker ----------------------------------------------------------------

    def _drain(self) -> None:
        try:
            while True:
                name, fmt, data, end_s = self._queue.get_nowait()
                samples = _downmix(data, fmt.channels)
                pending = self._pending.setdefault(name, _Pending(fmt.sample_rate))
                pending.add(samples, end_s)
        except queue.Empty:
            return

    def _cut(self, pending: _Pending) -> int:
        """Frames to send now: up to the quietest block of the window's end."""
        rate = pending.rate
        window = int(self._window_s * rate)
        data = np.concatenate(pending.chunks) if len(pending.chunks) > 1 else pending.chunks[0]
        pending.chunks, pending.frames = [data], len(data)
        search = min(window // 3, int(CUT_SEARCH_S * rate))  # a window is never cut short by more than a third
        block = max(1, int(BLOCK_S * rate))
        rms = _block_rms(data[window - search : window], block)
        if len(rms) == 0:
            return window
        return window - search + (int(np.argmin(rms)) + 1) * block

    def _run(self) -> None:
        while not self._stop.is_set():
            self._drain()
            worked = False
            for name, pending in list(self._pending.items()):
                if self._stop.is_set():
                    break
                if pending.frames < int(self._window_s * pending.rate):
                    continue
                at_s = pending.at_s
                window = pending.take(self._cut(pending))
                if pending.frames > MAX_BACKLOG_S * pending.rate:
                    lost = pending.frames - int(self._window_s * pending.rate)
                    pending.take(lost)
                    with self._lock:
                        self._skipped_s += lost / pending.rate
                worked = True
                self._transcribe_window(name, window, pending.rate, at_s)
            if not worked:
                self._stop.wait(IDLE_WAIT_S)

    def _transcribe_window(self, name: str, window: np.ndarray, rate: int, at_s: float) -> None:
        block = max(1, int(BLOCK_S * rate))
        loudest = _block_rms(window, block)
        if len(loudest) == 0 or loudest.max() < SILENCE_RMS:
            return
        started = self._clock()
        try:
            text = self._transcribe(_wav_bytes(_resample(window.astype(np.float32), rate))).strip()
            error = None
        except Exception as exc:  # a failed window is lost, the next one may work
            logger.warning("Live transcription of a %s window failed: %s", name, exc)
            text, error = "", str(exc)
        busy = self._clock() - started
        with self._lock:
            self._busy_s += busy
            self._windows += 1
            self._error = error
            if text:
                self._segments.append(
                    {
                        "n": len(self._segments),
                        "speaker": name,
                        "label": LABELS.get(name, name),
                        "text": text,
                        "at_s": int(at_s),
                    }
                )
        logger.debug("Live window %s: %.1f s of audio in %.2f s", name, len(window) / rate, busy)
