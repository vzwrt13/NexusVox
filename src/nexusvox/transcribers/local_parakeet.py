"""In-process Parakeet TDT 0.6B v3 for CPU (ONNX, int8) through onnx-asr.

The CPU counterpart of the Docker NeMo container: same model, no GPU and no Docker. Loads the
model once and caches it at the class level. The int8 ONNX build (~670 MB) is downloaded from
Hugging Face on first use; `load_status` reports that to the dashboard.

One model call handles roughly 20-30 s of audio, so longer audio (file uploads) is cut into
speech segments by onnx-asr's Silero VAD first and the segments' text and word timings are joined.
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
# Audio up to this long goes to the model in one call; longer audio is cut by the VAD, whose
# segments never exceed the same length.
MAX_DIRECT_S = 20.0
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


class LocalParakeetTranscriber(BaseTranscriber):
    """Transcribe with Parakeet TDT 0.6B v3 (ONNX int8) in-process. No HTTP, no Docker, no GPU."""

    needs_docker = False
    supports_word_timestamps = True
    no_diarization_reason = SPEAKERS_NEED_GPU

    _model_cache: dict[tuple[str, str], object] = {}
    _vad_cache: dict[str, object] = {}
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

    @classmethod
    def _get_vad(cls):
        """Silero VAD from onnx-asr (a few MB), loaded on the first audio longer than `MAX_DIRECT_S`."""
        with cls._cache_lock:
            if "silero" not in cls._vad_cache:
                import onnx_asr

                cls._vad_cache["silero"] = onnx_asr.load_vad("silero", providers=["CPUExecutionProvider"])
            return cls._vad_cache["silero"]

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

        # Too long for one call: the VAD cuts it into speech segments of at most MAX_DIRECT_S.
        segmented = model.with_vad(self._get_vad(), max_speech_duration_s=MAX_DIRECT_S)
        if self.word_timestamps:
            segmented = segmented.with_timestamps()
        texts: list[str] = []
        words = []
        for segment in segmented.recognize(samples, sample_rate=SAMPLE_RATE):
            if cancelled.is_set():
                return "", []
            texts.append(segment.text.strip())
            if self.word_timestamps:
                words.extend(words_from_tokens(segment.tokens or [], segment.timestamps or [], segment.start))
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
