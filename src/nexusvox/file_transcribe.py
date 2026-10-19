"""Audio file conversion and transcription for uploaded files."""

from __future__ import annotations

import io
import logging
import wave

from .config import MODEL_REGISTRY, InferenceConfig
from .transcriber import TranscriptionResult, Word, create_transcriber

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".webm"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_CHUNK_MS = 60_000  # 60 seconds per chunk — safe for 16 GB VRAM
_SEND_CHUNK_BYTES = 4096


def _segment_to_wav(segment) -> bytes:
    """Build a minimal WAV from a pydub AudioSegment (no extra metadata chunks)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16_000)
        wf.writeframes(segment.raw_data)
    return buf.getvalue()


def convert_to_wav(file_bytes: bytes, filename: str) -> tuple[list[bytes], int]:
    """Decode an audio file to 16kHz mono PCM16 WAV chunks.

    Long files are split into chunks of MAX_CHUNK_MS to avoid GPU OOM.
    Returns (wav_chunks, total_duration_ms).
    """
    from pydub import AudioSegment

    ext = _get_extension(filename)
    format_hint = {
        ".wav": "wav",
        ".mp3": "mp3",
        ".flac": "flac",
        ".ogg": "ogg",
        ".webm": "webm",
    }.get(ext, ext.lstrip("."))

    audio = AudioSegment.from_file(io.BytesIO(file_bytes), format=format_hint)
    audio = audio.set_frame_rate(16_000).set_channels(1).set_sample_width(2)
    total_duration_ms = len(audio)

    chunks = []
    for start in range(0, total_duration_ms, MAX_CHUNK_MS):
        segment = audio[start : start + MAX_CHUNK_MS]
        chunks.append(_segment_to_wav(segment))

    logger.info("Split %d ms audio into %d chunk(s)", total_duration_ms, len(chunks))
    return chunks, total_duration_ms


async def transcribe_file(
    wav_chunks: list[bytes],
    config: InferenceConfig,
    *,
    device: str | None = None,
    language: str | None = None,
    auto_detect_language: bool = False,
    word_timestamps: bool = False,
) -> TranscriptionResult:
    """Transcribe WAV chunks via the shared transcriber factory and join results.

    With `word_timestamps`, engines that support it also return per-word timing, shifted to
    the file's timeline in the joined result.

    Dispatches through `create_transcriber` so CPU users (in-process Whisper)
    and GPU users (Docker-backed HTTP/WS) share one code path.
    """
    transcriber = create_transcriber(
        config,
        device=device,
        language=language,
        auto_detect_language=auto_detect_language,
    )
    # Both the model's registry entry and the client must support words; a custom server or Cohere
    # shares the Parakeet client but never sees the fields.
    wants_words = (
        word_timestamps
        and bool(MODEL_REGISTRY.get(config.model, {}).get("word_timestamps"))
        and transcriber.supports_word_timestamps
    )
    if wants_words:
        transcriber.word_timestamps = True
    texts: list[str] = []
    words: list[Word] = []
    notes: list[str] = []
    offset_s = 0.0  # start of the current chunk on the file's timeline
    try:
        for i, wav_bytes in enumerate(wav_chunks):
            logger.info("Transcribing chunk %d/%d", i + 1, len(wav_chunks))
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                pcm_data = wf.readframes(wf.getnframes())

            await transcriber.connect()
            for offset in range(0, len(pcm_data), _SEND_CHUNK_BYTES):
                await transcriber.send_audio_chunk(pcm_data[offset : offset + _SEND_CHUNK_BYTES])
            result = await transcriber.finish(timeout=120.0)
            texts.append(result.text)

            label = f"Chunk {i + 1} of {len(wav_chunks)}"
            if not result.done:
                notes.append(f"{label} timed out; its text may be incomplete or missing.")
            elif wants_words and result.text and not result.words:
                notes.append(f"{label} came back without word timestamps.")
            words.extend(Word(w.word, w.start + offset_s, w.end + offset_s) for w in result.words)
            offset_s += len(pcm_data) / 2 / 16_000  # PCM16 mono at 16 kHz
    finally:
        await transcriber.disconnect()

    for note in notes:
        logger.warning(note)
    combined = TranscriptionResult()
    combined.finalize(text=" ".join(t for t in texts if t).strip())
    combined.words = words
    combined.notes = notes
    return combined


def _get_extension(filename: str) -> str:
    """Return the lowercase file extension including the dot."""
    dot = filename.rfind(".")
    if dot == -1:
        return ""
    return filename[dot:].lower()
