"""In-process Parakeet TDT 0.6B v3 for CPU (ONNX, int8) through onnx-asr.

The CPU counterpart of the Docker NeMo container: same model, no GPU and no Docker. Loads the
model once and caches it at the class level. The int8 ONNX build (~670 MB) is downloaded from
Hugging Face on first use; `load_status` reports that to the dashboard.

One model call handles roughly 20 s of audio well, so longer audio (long dictations, file uploads) is
cut at speech pauses into segments of at most `MAX_DIRECT_S` (`split_at_pauses`) and each segment goes
through the same plain model call a short dictation uses; the segments' text and word timings are joined.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..config import MODEL_REGISTRY, InferenceConfig
from ..logsetup import Text
from ..transcriber import SPEAKERS_NEED_GPU, BaseTranscriber, TranscriptionResult, Word

logger = logging.getLogger(__name__)

# Where onnx-asr fetches the model from, and the file whose presence in the Hugging Face cache
# means the download is done (the encoder is the big one).
MODEL_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
_CACHE_PROBE_FILE = "encoder-model.int8.onnx"

SAMPLE_RATE = 16_000
# Audio up to this long goes to the model in one call; longer audio is cut at pauses into segments
# that never exceed the same length.
MAX_DIRECT_S = 20.0
# A segment cut at a pause is not shorter than this unless the audio ends: the model needs a few
# seconds of context to keep the language, and onnx-asr's VAD cut far shorter pieces that drifted.
MIN_SEGMENT_S = 6.0
# A segment whose loudest sample stays below this (about -50 dBFS) holds no speech and is not decoded.
_SILENCE_PEAK = 0.003
# Pause detection works on 20 ms frames; a pause is a run of at least this many quiet seconds.
_PAUSE_FRAME = SAMPLE_RATE // 50
_MIN_PAUSE_S = 0.2
# Each second a cut sits later in the window is worth this many seconds of pause: among similar
# pauses the later cut wins, so segments come out long.
_LATE_CUT_BONUS = 0.05
# Less than this is silence for all practical purposes and trips the feature extractor.
_MIN_SAMPLES = SAMPLE_RATE // 10
# One encoder frame: a 10 ms feature step times the model's subsampling factor of 8.
FRAME_S = 0.08
# SentencePiece marks the first piece of a word with this character; onnx-asr already turns it
# into a space, and both spellings are accepted.
_WORD_START = "▁"
# onnx-asr drops the space before a character that is not a word character ("Hello ," -> "Hello,"), so
# a token like " ," or " -" belongs to the word before it, in the words as in the text.
_WORD_CHAR = re.compile(r"\w")


def words_from_tokens(tokens: list[str], timestamps: list[float], offset_s: float = 0.0) -> list[Word]:
    """Group a model's tokens into words with timing, in seconds from `offset_s`.

    A token that starts with the SentencePiece word mark (or a space) and then a word character opens
    a new word; the rest continue the current one. Punctuation stays attached to the word before it,
    also when its token carries the word mark, which is how onnx-asr writes the text too. A word
    starts when its first token is emitted and ends one frame after its last token, never later
    than the next word starts.
    """
    pieces: list[tuple[str, float, float]] = []  # (text, first token time, last token time)
    new_word = True
    for token, stamp in zip(tokens, timestamps, strict=False):
        if token.startswith((_WORD_START, " ")):
            new_word = True
        text = token.lstrip(_WORD_START + " ")
        if not text:
            continue  # a bare word mark: only the word break counts
        if not pieces or (new_word and _WORD_CHAR.match(text)):
            pieces.append((text, stamp, stamp))
        else:
            word, first, _last = pieces[-1]
            pieces[-1] = (word + text, first, stamp)
        new_word = False

    words: list[Word] = []
    for i, (text, first, last) in enumerate(pieces):
        end = last + FRAME_S
        if i + 1 < len(pieces):
            end = min(end, pieces[i + 1][1])
        words.append(Word(text, offset_s + first, offset_s + max(end, first)))
    return words


def _frame_levels(samples: np.ndarray) -> np.ndarray:
    """RMS level of every 20 ms frame; a ragged tail shorter than a frame is dropped."""
    frames = len(samples) // _PAUSE_FRAME
    if frames == 0:
        return np.zeros(0, dtype=np.float32)
    block = samples[: frames * _PAUSE_FRAME].reshape(frames, _PAUSE_FRAME)
    return np.sqrt(np.mean(block * block, axis=1))


def split_at_pauses(
    samples: np.ndarray, max_s: float | None = None, min_s: float | None = None
) -> list[tuple[int, int]]:
    """Cut mono 16 kHz audio into (start, end) sample ranges of at most `max_s`, at speech pauses.

    Audio up to `max_s` stays whole. Otherwise each cut goes into the middle of a pause (a run of quiet
    20 ms frames, quiet being a few times the recording's own noise floor) in the window `min_s`..`max_s`
    after the segment start: the longest pause, with a small bonus for a later cut. A window with no pause
    is cut at its quietest frame, so a continuous stream is still bounded and never cut mid-loud-passage
    if a quieter spot exists. The ranges are contiguous and cover all the audio.
    """
    max_s = MAX_DIRECT_S if max_s is None else max_s
    min_s = min(MIN_SEGMENT_S, max_s / 2) if min_s is None else min_s
    total = len(samples)
    max_len = int(max_s * SAMPLE_RATE)
    if total <= max_len:
        return [(0, total)]

    level = _frame_levels(samples)
    floor, loud = np.percentile(level, [10, 90])
    quiet = level < min(max(floor * 3.0, 0.002), max(loud * 0.3, 0.002))
    min_pause = max(1, round(_MIN_PAUSE_S * SAMPLE_RATE / _PAUSE_FRAME))
    frames_per_s = SAMPLE_RATE / _PAUSE_FRAME

    ranges: list[tuple[int, int]] = []
    start = 0
    while total - start > max_len:
        lo = (start + int(min_s * SAMPLE_RATE)) // _PAUSE_FRAME
        hi = min((start + max_len) // _PAUSE_FRAME, len(level))
        cut = _best_cut(quiet, level, lo, hi, min_pause, frames_per_s)
        end = cut * _PAUSE_FRAME if cut is not None else start + max_len
        end = min(max(end, start + int(min_s * SAMPLE_RATE)), start + max_len)
        ranges.append((start, end))
        start = end
    ranges.append((start, total))
    return ranges


def _best_cut(
    quiet: np.ndarray, level: np.ndarray, lo: int, hi: int, min_pause: int, frames_per_s: float
) -> int | None:
    """Frame index to cut at inside frames [lo, hi), or None when the window holds no frames."""
    if hi <= lo:
        return None
    best: tuple[float, int] | None = None
    run_start: int | None = None
    for i in range(lo, hi + 1):
        if i < hi and quiet[i]:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None and i - run_start >= min_pause:
            length_s = (i - run_start) / frames_per_s
            middle = (run_start + i) // 2
            score = length_s + _LATE_CUT_BONUS * (middle - lo) / frames_per_s
            if best is None or score >= best[0]:
                best = (score, middle)
        run_start = None
    if best is not None:
        return best[1]
    return lo + int(np.argmin(level[lo:hi]))  # no pause at all: the quietest spot


class LocalParakeetTranscriber(BaseTranscriber):
    """Transcribe with Parakeet TDT 0.6B v3 (ONNX int8) in-process. No HTTP, no Docker, no GPU."""

    needs_docker = False
    supports_word_timestamps = True
    no_diarization_reason = SPEAKERS_NEED_GPU

    _model_cache: dict[tuple[str, str], object] = {}
    _cache_lock = threading.Lock()
    # onnx-asr's decoding is plain Python over the ONNX sessions: one recognition at a time keeps a
    # dictation and an upload from fighting over the CPU cores. A single worker serialises them and
    # keeps them off the loop's default pool, which docker_ctl and Whisper use too.
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet")
    _status: dict[str, str | None] = {"state": "idle", "error": None}
    # True while a background load runs, so connect() queues one at a time.
    _preloading = False
    _state_lock = threading.Lock()

    def __init__(self, config: InferenceConfig) -> None:
        registry = MODEL_REGISTRY[config.model]
        self._onnx_model = str(registry["onnx_model"])
        self._quantization = str(registry["onnx_quantization"])
        self._audio_buffer = bytearray()
        self._connected = False
        self.model = str(registry["hf_name"])
        # A new transcriber (start, model switch) gets a fresh try after an earlier failed load.
        with self._state_lock:
            if self._status["state"] == "error":
                self._set_status("idle")

    # ---- Model loading ------------------------------------------------------

    @classmethod
    def load_status(cls) -> dict[str, str | None]:
        """`state` is idle, downloading, loading, ready or error; `error` says why after a failed load.

        Takes no lock: `_cache_lock` is held for the whole download, and the dashboard polls this meanwhile.
        """
        return dict(cls._status)

    @classmethod
    def _set_status(cls, state: str, error: str | None = None) -> None:
        cls._status = {"state": state, "error": error}

    @staticmethod
    def _is_downloaded() -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache

            return isinstance(try_to_load_from_cache(MODEL_REPO, _CACHE_PROBE_FILE), str)
        except Exception:
            return True  # cannot tell; report "loading" rather than a download that may not happen

    @classmethod
    def _get_model(cls, onnx_model: str, quantization: str):
        """Load or reuse the cached onnx-asr model; downloads it first on first use."""
        key = (onnx_model, quantization)
        with cls._cache_lock:
            if key in cls._model_cache:
                return cls._model_cache[key]
            cls._set_status("loading" if cls._is_downloaded() else "downloading")
            try:
                import onnx_asr

                logger.info(
                    "%s Parakeet model %s (%s)...",
                    "Loading" if cls._status["state"] == "loading" else "Downloading and loading (~670 MB)",
                    onnx_model,
                    quantization,
                )
                cls._model_cache[key] = onnx_asr.load_model(
                    onnx_model, quantization=quantization, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:
                cls._set_status("error", str(exc))
                raise
            cls._set_status("ready")
            logger.info("Parakeet model ready")
            return cls._model_cache[key]

    # ---- Transcription ------------------------------------------------------

    def _recognize(self, samples: np.ndarray, cancelled: threading.Event) -> tuple[str, list[Word]]:
        """Text and word timing of mono 16 kHz float samples; blocking, call from the worker thread.

        `cancelled` is set when the caller gave up (timeout); the job then stops at the next safe point, so
        abandoned work does not make the dictations behind it wait for results nobody reads.
        """
        if len(samples) < _MIN_SAMPLES or cancelled.is_set():
            return "", []
        model = self._get_model(self._onnx_model, self._quantization)
        if cancelled.is_set():
            return "", []
        if len(samples) <= MAX_DIRECT_S * SAMPLE_RATE:
            if not self.word_timestamps:
                return model.recognize(samples, sample_rate=SAMPLE_RATE).strip(), []
            result = model.with_timestamps().recognize(samples, sample_rate=SAMPLE_RATE)
            words = words_from_tokens(result.tokens or [], result.timestamps or [])
            return result.text.strip(), words

        # Too long for one call: cut at pauses, then every segment is a plain call like a short dictation.
        texts: list[str] = []
        words: list[Word] = []
        timed = model.with_timestamps() if self.word_timestamps else model
        for start, end in split_at_pauses(samples):
            if cancelled.is_set():
                return "", []
            chunk = samples[start:end]
            if len(chunk) < _MIN_SAMPLES or float(np.max(np.abs(chunk))) < _SILENCE_PEAK:
                continue  # nothing said: the model would only make something up
            if self.word_timestamps:
                result = timed.recognize(chunk, sample_rate=SAMPLE_RATE)
                texts.append(result.text.strip())
                words.extend(words_from_tokens(result.tokens or [], result.timestamps or [], start / SAMPLE_RATE))
            else:
                texts.append(model.recognize(chunk, sample_rate=SAMPLE_RATE).strip())
        return " ".join(t for t in texts if t), words

    async def connect(self) -> None:
        self._audio_buffer = bytearray()
        self._connected = True
        # Trigger a background load so the first recording doesn't pay the full download and
        # load latency inside finish().
        if self._begin_preload():
            asyncio.get_event_loop().run_in_executor(None, self._preload)

    def _begin_preload(self) -> bool:
        """Claim the one background load; False when the model is cached, a load runs, or the last one failed.

        After a failure the next `finish()` makes the one retry, so a press does not cost two download attempts.
        The status is set here, before the load thread starts, so the dashboard never sees a gap.
        """
        with self._state_lock:
            if (self._onnx_model, self._quantization) in self._model_cache or LocalParakeetTranscriber._preloading:
                return False
            if self._status["state"] == "error":
                return False
            LocalParakeetTranscriber._preloading = True
            self._set_status("loading" if self._is_downloaded() else "downloading")
            return True

    def _preload(self) -> None:
        try:
            self._get_model(self._onnx_model, self._quantization)
        except Exception:
            logger.exception("Loading the Parakeet model failed")
        finally:
            LocalParakeetTranscriber._preloading = False

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        if not self._connected:
            raise RuntimeError("Not connected")
        self._audio_buffer.extend(pcm16_bytes)

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        if not self._connected:
            raise RuntimeError("Not connected")

        pcm = bytes(self._audio_buffer)
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        logger.info("Running in-process Parakeet on %.1f s of audio", len(samples) / SAMPLE_RATE)

        result = TranscriptionResult()
        loop = asyncio.get_event_loop()
        cancelled = threading.Event()
        try:
            text, words = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._recognize, samples, cancelled), timeout=timeout
            )
            result.finalize(text=text)
            result.words = words
            logger.info("Transcription done: %s", Text(result.text))
        except TimeoutError:
            logger.warning("In-process transcription timed out after %.0fs", timeout)
        finally:
            # Stops a job still queued or running (a timeout, a cancelled task); a no-op once it is done.
            cancelled.set()
            # Also after a load or ONNX error, which propagates: no stale audio, no half-open recording.
            self._connected = False
            self._audio_buffer = bytearray()
        return result

    async def disconnect(self) -> None:
        self._audio_buffer = bytearray()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected
