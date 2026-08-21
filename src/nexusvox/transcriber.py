"""Transcriber abstraction with implementations for different model protocols."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import wave
from abc import ABC, abstractmethod

import websockets
from websockets.connection import State

from .config import MODEL_REGISTRY, InferenceConfig, resolve_compute_type, resolve_device

logger = logging.getLogger(__name__)


class TranscriptionResult:
    """Accumulates streaming transcription tokens."""

    def __init__(self) -> None:
        self.text = ""
        self.done = False
        self.usage: dict | None = None
        self.confidence: float | None = None
        self._logprobs: list[float] = []

    def append(self, delta: str, logprob: float | None = None) -> None:
        self.text += delta
        if logprob is not None:
            self._logprobs.append(logprob)

    def finalize(
        self,
        text: str,
        usage: dict | None = None,
        logprobs: list[float] | None = None,
    ) -> None:
        self.text = text
        self.done = True
        self.usage = usage

        # Merge any logprobs from the done message
        if logprobs:
            self._logprobs.extend(logprobs)

        # Compute average confidence from accumulated logprobs
        if self._logprobs:
            avg_logprob = sum(self._logprobs) / len(self._logprobs)
            self.confidence = math.exp(avg_logprob)
            logger.debug(
                "Confidence: %.4f (from %d tokens, avg logprob %.4f)",
                self.confidence,
                len(self._logprobs),
                avg_logprob,
            )


def pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = 16_000) -> bytes:
    """Wrap raw PCM16 mono bytes in a minimal in-memory WAV container."""
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return wav_buffer.getvalue()


class BaseTranscriber(ABC):
    """Common interface for all transcription backends."""

    model: str
    needs_docker: bool = True

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None: ...

    @abstractmethod
    async def finish(self, timeout: float = 30.0) -> TranscriptionResult: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...


class VoxtralRealtimeTranscriber(BaseTranscriber):
    """Streams audio to vLLM's /v1/realtime WebSocket and receives transcription tokens.

    Usage per transcription cycle:
        await connect()
        for chunk in audio_chunks:
            await send_audio_chunk(chunk)
        result = await finish()
        # connection is closed, call connect() again for next cycle
    """

    def __init__(self, config: InferenceConfig) -> None:
        self._server_url = config.server_url
        self._ws: websockets.ClientConnection | None = None
        self.model = "mistralai/Voxtral-Mini-4B-Realtime-2602"

    async def connect(self) -> None:
        """Connect to the vLLM realtime endpoint and initialize session."""
        # Close any existing connection
        if self._ws is not None:
            await self.disconnect()

        self._ws = await websockets.connect(self._server_url)

        # Receive session.created
        msg = json.loads(await self._ws.recv())
        if msg.get("type") != "session.created":
            raise RuntimeError(f"Expected session.created, got: {msg}")
        logger.info("Session created: %s", msg.get("id"))

        # Send session configuration
        # NOTE: logprobs are not supported in vLLM's realtime WebSocket
        # protocol as of 2026-04. If support is added, include
        # "logprobs": True here and the extraction code in
        # TranscriptionResult will compute confidence automatically.
        await self._ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "model": self.model,
                }
            )
        )

        # Start the generation engine (non-final commit)
        await self._ws.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.commit",
                }
            )
        )

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        """Send a PCM16 audio chunk to the server."""
        if self._ws is None:
            raise RuntimeError("Not connected")

        audio_b64 = base64.b64encode(pcm16_bytes).decode("ascii")
        await self._ws.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": audio_b64,
                }
            )
        )

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        """Commit audio buffer and receive the full transcription.

        Closes the connection afterward so a fresh one is used per cycle.
        """
        if self._ws is None:
            raise RuntimeError("Not connected")

        # Signal end of audio
        logger.info("Committing audio buffer to server")
        await self._ws.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.commit",
                    "final": True,
                }
            )
        )

        # Collect transcription tokens
        result = TranscriptionResult()
        try:
            while not result.done:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "transcription.delta":
                    delta = msg.get("delta", "")
                    logprob = msg.get("logprob")
                    result.append(delta, logprob=logprob)
                    logger.debug("Delta: %s (logprob=%s)", delta, logprob)

                elif msg_type == "transcription.done":
                    logger.debug("transcription.done payload: %s", json.dumps(msg))
                    result.finalize(
                        text=msg.get("text", result.text),
                        usage=msg.get("usage"),
                        logprobs=msg.get("logprobs"),
                    )
                    logger.info("Transcription done: %s", result.text)

                elif msg_type == "error":
                    error = msg.get("error", "Unknown error")
                    logger.error("Server error: %s", error)
                    raise RuntimeError(f"Transcription error: {error}")

                else:
                    logger.info("Server message: %s", msg_type)
        except TimeoutError:
            logger.warning("Transcription timed out after %.0fs", timeout)

        await self.disconnect()
        return result

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.state == State.OPEN


class OpenAIHttpTranscriber(BaseTranscriber):
    """Buffers audio chunks, then POSTs complete WAV to an OpenAI-compatible endpoint.

    Used for models served via /v1/audio/transcriptions HTTP endpoint
    (e.g. Cohere Transcribe, Parakeet TDT).

    Usage per transcription cycle (same as VoxtralRealtimeTranscriber):
        await connect()
        for chunk in audio_chunks:
            await send_audio_chunk(chunk)
        result = await finish()
    """

    def __init__(self, config: InferenceConfig) -> None:
        self._server_url = config.server_url
        self._audio_buffer = bytearray()
        self._sample_rate = 16_000
        self.model = MODEL_REGISTRY[config.model]["hf_name"]
        self._connected = False

    async def connect(self) -> None:
        """Reset audio buffer for a new transcription cycle."""
        self._audio_buffer = bytearray()
        self._connected = True

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        """Buffer audio chunk in memory (sent as a batch in finish())."""
        if not self._connected:
            raise RuntimeError("Not connected")
        self._audio_buffer.extend(pcm16_bytes)

    async def finish(self, timeout: float = 30.0) -> TranscriptionResult:
        """Convert buffered PCM16 to WAV, POST to server, return transcription."""
        if not self._connected:
            raise RuntimeError("Not connected")

        import httpx

        wav_bytes = pcm16_to_wav_bytes(bytes(self._audio_buffer), sample_rate=self._sample_rate)

        logger.info("Sending %d bytes of WAV audio to %s", len(wav_bytes), self._server_url)

        result = TranscriptionResult()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self._server_url,
                    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                    data={"model": self.model},
                )
                response.raise_for_status()
                data = response.json()

            text = data.get("text", "")
            result.finalize(text=text)
            logger.info("Transcription done: %s", result.text)
        except httpx.TimeoutException:
            logger.warning("Transcription timed out after %.0fs", timeout)
        except httpx.HTTPStatusError as exc:
            logger.error("Server error: %s %s", exc.response.status_code, exc.response.text)
            raise RuntimeError(f"Transcription error: {exc.response.status_code}") from exc

        self._connected = False
        self._audio_buffer = bytearray()
        return result

    async def disconnect(self) -> None:
        """Clear buffer state."""
        self._audio_buffer = bytearray()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected


def create_transcriber(
    config: InferenceConfig,
    *,
    device: str | None = None,
    language: str | None = None,
    auto_detect_language: bool = False,
) -> BaseTranscriber:
    """Factory: create the right transcriber based on the configured model and device.

    Resolution precedence for device: explicit `device` kwarg > `config.device` > "auto".
    On CPU, whisper entries run in-process via LocalWhisperTranscriber (no Docker).
    GPU-only models (voxtral/cohere/parakeet) raise if selected with device=cpu.
    """
    registry = MODEL_REGISTRY.get(config.model)
    if registry is None:
        raise ValueError(f"Unknown model: {config.model}")

    resolved_device = resolve_device(device if device is not None else config.device)
    compute_type = resolve_compute_type(resolved_device, config.compute_type)

    if registry.get("inprocess_supported") and resolved_device == "cpu":
        from .transcribers.local_whisper import LocalWhisperTranscriber

        return LocalWhisperTranscriber(
            config,
            device=resolved_device,
            compute_type=compute_type,
            language=language,
            auto_detect_language=auto_detect_language,
        )

    if registry.get("requires_gpu") and resolved_device == "cpu":
        raise RuntimeError(
            f"Model {config.model!r} requires a GPU, but the resolved device is CPU. "
            "Switch to a CPU-capable model (e.g. whisper-large-v3-turbo or whisper-small) "
            "or set [inference].device = 'cuda' in config.toml."
        )

    protocol = registry["protocol"]
    if protocol == "realtime_ws":
        return VoxtralRealtimeTranscriber(config)
    elif protocol == "openai_http":
        return OpenAIHttpTranscriber(config)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")
