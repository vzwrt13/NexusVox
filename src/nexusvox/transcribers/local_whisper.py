"""In-process faster-whisper transcriber for CPU (and optional GPU) mode.

Loads the model once and caches it at the class level — switching between
Whisper entries (e.g. whisper-large-v3-turbo <-> whisper-small) reuses any
already-loaded instance. Model downloads happen on first use via faster-whisper's
HuggingFace Hub integration (~/.cache/huggingface on Linux/macOS,
%USERPROFILE%\\.cache\\huggingface on Windows).
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading

from ..config import MODEL_REGISTRY, InferenceConfig
from ..transcriber import BaseTranscriber, TranscriptionResult, pcm16_to_wav_bytes

logger = logging.getLogger(__name__)


class LocalWhisperTranscriber(BaseTranscriber):
    """Transcribe with faster-whisper in-process. No HTTP, no Docker."""

    needs_docker = False

    _model_cache: dict[tuple[str, str, str], object] = {}
    _cache_lock = threading.Lock()

    def __init__(
        self,
        config: InferenceConfig,
        device: str,
        compute_type: str,
        language: str | None = None,
        auto_detect_language: bool = False,
    ) -> None:
        registry = MODEL_REGISTRY[config.model]
        self._hf_name = str(registry["hf_name"])
        self._device = device
        self._compute_type = compute_type
        self._language: str | None = None if auto_detect_language else language
        self._audio_buffer = bytearray()
        self._sample_rate = 16_000
        self._connected = False
        self.model = self._hf_name

    @classmethod
    def _get_model(cls, hf_name: str, device: str, compute_type: str):
        """Load or reuse a cached WhisperModel for the given parameters."""
        key = (hf_name, device, compute_type)
        with cls._cache_lock:
            if key not in cls._model_cache:
                from faster_whisper import WhisperModel

                logger.info(
                    "Loading faster-whisper model %s on %s (%s)...",
                    hf_name,
                    device,
                    compute_type,
                )
                cls._model_cache[key] = WhisperModel(
                    hf_name,
                    device=device,
                    compute_type=compute_type,
                )
                logger.info("faster-whisper model ready")
            return cls._model_cache[key]

    async def connect(self) -> None:
        self._audio_buffer = bytearray()
        self._connected = True
        # Trigger a background load so the first recording doesn't pay the
        # full model-load latency inside finish().
        loop = asyncio.get_event_loop()
        key = (self._hf_name, self._device, self._compute_type)
        if key not in self._model_cache:
            loop.run_in_executor(None, self._get_model, self._hf_name, self._device, self._compute_type)

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        if not self._connected:
            raise RuntimeError("Not connected")
        self._audio_buffer.extend(pcm16_bytes)

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        if not self._connected:
            raise RuntimeError("Not connected")

        wav_bytes = pcm16_to_wav_bytes(bytes(self._audio_buffer), sample_rate=self._sample_rate)
        logger.info("Running in-process Whisper on %d bytes of WAV", len(wav_bytes))

        result = TranscriptionResult()
        loop = asyncio.get_event_loop()

        def _transcribe() -> str:
            model = self._get_model(self._hf_name, self._device, self._compute_type)
            segments, _info = model.transcribe(
                io.BytesIO(wav_bytes),
                beam_size=1,
                language=self._language,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

        try:
            text = await asyncio.wait_for(loop.run_in_executor(None, _transcribe), timeout=timeout)
            result.finalize(text=text)
            logger.info("Transcription done: %s", result.text)
        except TimeoutError:
            logger.warning("In-process transcription timed out after %.0fs", timeout)

        self._connected = False
        self._audio_buffer = bytearray()
        return result

    async def disconnect(self) -> None:
        self._audio_buffer = bytearray()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected
