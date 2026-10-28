"""A rough transcript of a meeting while it records, shown in the dashboard with speaker labels.

The recorder hands every audio block of both tracks to `LiveTranscriber.feed`. A worker thread collects each track
into windows of about `WINDOW_S` seconds, cut at the quietest moment of the window's last two seconds so a word is
rarely split, and sends one window at a time through the transcription callback. Silent windows are not sent, a
track the worker cannot keep up with drops its oldest audio, and the tap never blocks the audio thread.

This transcript is only a preview: nothing is stored, the files on disk are untouched, and the job that runs after
Stop still produces the stored transcript. Live text has no echo filter, no speaker detection and no dictation
filter, so the microphone may repeat what the speakers play.
"""

from __future__ import annotations

import asyncio
import io
import logging
import queue
import threading
import time
import wave
from collections.abc import Callable

import numpy as np

from .config import DEFAULT_OWN_VOICE_LABEL
from .meeting_recorder import AudioFormat
from .meeting_transcribe import _resample, _wav_bytes, block_power, downmix
from .transcriber import BaseTranscriber

logger = logging.getLogger(__name__)

WINDOW_S = 6.0  # audio sent per request; with the request time this is the delay of the live text
CUT_SEARCH_S = 2.0  # the window is cut at the quietest block within its last seconds
BLOCK_S = 0.05
MAX_BACKLOG_S = 30.0  # a track further behind than this drops its oldest audio instead of falling behind for good
SILENCE_RMS = 100.0  # a window whose loudest 50 ms block stays below this is not sent
IDLE_WAIT_S = 0.25
LIVE_TIMEOUT_S = 20.0  # a live window the server has not answered by then is lost; the worker moves on
_SEND_CHUNK_BYTES = 4096


# Live only: the system track has no speaker detection while recording and may hold several people, so it gets one
# neutral label. The stored transcript made after Stop numbers them (Speaker 2, 3, ...).
LIVE_OTHERS_LABEL = "Others"


def default_labels() -> dict[str, str]:
    """The microphone is the own voice (Speaker 1 unless named), everyone on the system track is `Others`."""
    return {"mic": DEFAULT_OWN_VOICE_LABEL, "system": LIVE_OTHERS_LABEL}


def _block_rms(samples: np.ndarray, block: int) -> np.ndarray:
    return np.sqrt(block_power(samples, block))


class _Pending:
    """The audio of one track that has not been sent yet (mono float32 at the device rate)."""

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


class WindowTranscriber:
    """Transcribes live windows with one transcriber and one event loop that live as long as the meeting.

    Called from the live worker thread only (the loop belongs to it); `LiveTranscriber` calls `close()` when the
    worker ends. A window the server answers late or incompletely raises, so it counts as failed.
    """

    def __init__(self, make: Callable[[], BaseTranscriber], timeout: float = LIVE_TIMEOUT_S) -> None:
        self._make = make
        self._timeout = timeout
        self._transcriber: BaseTranscriber | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def __call__(self, wav: bytes) -> str:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._transcriber = self._make()
        return self._loop.run_until_complete(self._one(wav))

    async def _one(self, wav: bytes) -> str:
        assert self._transcriber is not None
        with wave.open(io.BytesIO(wav), "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
        await self._transcriber.connect()
        for offset in range(0, len(pcm), _SEND_CHUNK_BYTES):
            await self._transcriber.send_audio_chunk(pcm[offset : offset + _SEND_CHUNK_BYTES])
        result = await self._transcriber.finish(timeout=self._timeout)
        if not result.done:
            raise TimeoutError(f"the server did not answer within {self._timeout:.0f} s")
        return result.text

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            if self._transcriber is not None:
                self._loop.run_until_complete(self._transcriber.disconnect())
        finally:
            self._loop.close()
            self._loop = self._transcriber = None


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
        labels: Callable[[], dict[str, str]] = default_labels,
    ) -> None:
        self._transcribe = transcribe
        self._labels = labels
        self._clock = clock
        self._window_s = window_s
        # Unbounded on purpose (`put` never blocks the audio thread): the worker drains it before every window, so it
        # holds at most the audio of one transcription call, and the backlog limit is applied on the drained audio.
        self._queue: queue.SimpleQueue[tuple[str, AudioFormat, bytes, float]] = queue.SimpleQueue()
        self._pause: Callable[[], bool] = lambda: False
        self._pending: dict[str, _Pending] = {}
        self._segments: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="meeting-live", daemon=True)
        self._t0 = clock()  # the meeting's clock zero: the recorder feeds blocks before `start()` runs
        self._busy_s = 0.0
        self._windows = 0
        self._skipped_s = 0.0
        self._error: str | None = None

    @property
    def active(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def pause_while(self, paused: Callable[[], bool]) -> None:
        """Hold back new windows while `paused()` is true (a dictation take is running: it comes first)."""
        self._pause = paused

    def stop(self, timeout: float = 5.0) -> None:
        """End the worker; the window being transcribed finishes (at most `timeout` seconds are waited)."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def feed(self, name: str, fmt: AudioFormat, data: bytes) -> None:
        """Audio-thread entry: never blocks."""
        if not self._stop.is_set():
            self._queue.put((name, fmt, data, self._clock() - self._t0))

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
        """Move the fed blocks into the per-track pending audio, then drop what a track is too far behind with."""
        try:
            while True:
                name, fmt, data, end_s = self._queue.get_nowait()
                pending = self._pending.setdefault(name, _Pending(fmt.sample_rate))
                pending.add(downmix(data, fmt.channels), end_s)
        except queue.Empty:
            pass
        for pending in self._pending.values():
            if pending.frames > MAX_BACKLOG_S * pending.rate:
                lost = pending.frames - int(self._window_s * pending.rate)
                pending.take(lost)
                with self._lock:
                    self._skipped_s += lost / pending.rate

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

    def _ready(self) -> tuple[str, _Pending] | None:
        """The track with a full window that has waited longest."""
        ready = [(p.at_s, n, p) for n, p in self._pending.items() if p.frames >= int(self._window_s * p.rate)]
        if not ready:
            return None
        _, name, pending = min(ready, key=lambda r: r[0])
        return name, pending

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                # Drained (and trimmed) before every window, so a slow transcription never lets audio pile up unseen.
                self._drain()
                found = None if self._pause() else self._ready()
                if found is None:
                    self._stop.wait(IDLE_WAIT_S)
                    continue
                name, pending = found
                at_s = pending.at_s
                window = pending.take(self._cut(pending))
                self._transcribe_window(name, window, pending.rate, at_s)
        finally:
            close = getattr(self._transcribe, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    logger.exception("Closing the live transcriber failed")

    def _transcribe_window(self, name: str, window: np.ndarray, rate: int, at_s: float) -> None:
        block = max(1, int(BLOCK_S * rate))
        loudest = _block_rms(window, block)
        if len(loudest) == 0 or loudest.max() < SILENCE_RMS:
            return
        started = self._clock()
        try:
            text = self._transcribe(_wav_bytes(_resample(window, rate))).strip()
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
                        "label": self._labels().get(name, name),
                        "text": text,
                        "at_s": int(at_s),
                    }
                )
        logger.debug("Live window %s: %.1f s of audio in %.2f s", name, len(window) / rate, busy)
