"""Audio file conversion and transcription for uploaded files."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import wave
from collections.abc import Callable

from .config import MODEL_REGISTRY, InferenceConfig
from .diarization import diarization_timeout, format_transcript, label_words, speaker_detection_models
from .transcriber import BaseTranscriber, SpeakerTurn, TranscriptionResult, Word, create_transcriber

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".webm"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_CHUNK_MS = 60_000  # 60 seconds per chunk — safe for 16 GB VRAM
_SEND_CHUNK_BYTES = 4096
# Ends every note that says why speaker detection gave nothing; a meeting swaps it for what its transcript does.
NO_SPEAKER_LABELS = "the transcript has no speaker labels."
# One speaker-detection request at a time from this app: the server runs them one after the other
# anyway, and a request's timeout must not include the time it waits behind another upload's run.
_DIARIZE_LOCK = threading.Lock()


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
    diarize: bool = False,
    on_progress: Callable[..., None] | None = None,
) -> TranscriptionResult:
    """Transcribe WAV chunks via the shared transcriber factory and join results.

    With `word_timestamps`, engines that support it also return per-word timing, shifted to
    the file's timeline in the joined result. With `diarize` (implies word timestamps), engines
    that support it also detect speakers once over the whole file; the text then becomes
    "Speaker 1: ..." paragraphs and each word carries its speaker. When speaker detection is not
    possible or fails, the plain transcript comes back with a note saying why.

    `on_progress(stage, current, total)` is called as the work moves on: `"transcribing"` as chunk
    `current` of `total` starts, `"diarizing"` when only speaker detection is left.

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
    entry = MODEL_REGISTRY.get(config.model, {})
    wants_words = (
        (word_timestamps or diarize) and bool(entry.get("word_timestamps")) and transcriber.supports_word_timestamps
    )
    if wants_words:
        transcriber.word_timestamps = True
    wants_speakers = diarize and wants_words and bool(entry.get("diarization")) and transcriber.supports_diarization
    texts: list[str] = []
    words: list[Word] = []
    notes: list[str] = []
    words_complete = True  # every word of every chunk's text came back with its timing
    offset_s = 0.0  # start of the current chunk on the file's timeline
    # Speaker detection does not need the transcript, so it runs while the chunks are transcribed.
    speakers = asyncio.create_task(_diarize_file(transcriber, wav_chunks)) if wants_speakers else None
    try:
        for i, wav_bytes in enumerate(wav_chunks):
            logger.info("Transcribing chunk %d/%d", i + 1, len(wav_chunks))
            if on_progress:
                on_progress("transcribing", i + 1, len(wav_chunks))
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
            # The server skips a word whose timing it cannot read, so count them against the text.
            words_complete = words_complete and len(result.words) >= len(result.text.split())
            words.extend(Word(w.word, w.start + offset_s, w.end + offset_s) for w in result.words)
            offset_s += len(pcm_data) / 2 / 16_000  # PCM16 mono at 16 kHz

        text = " ".join(t for t in texts if t).strip()
        if diarize and not wants_speakers:
            notes.append(_unavailable_note(entry, transcriber))
        elif speakers is not None and text:
            if on_progress and not speakers.done():
                on_progress("diarizing")
            turns, problem = await speakers
            if not words_complete:
                # Labelled paragraphs are built from the words; a word without timing would vanish.
                notes.append("Speaker detection skipped: some words came back without timing.")
            elif problem:
                notes.append(problem)
            else:
                words = label_words(words, turns)
                text = format_transcript(words)
    finally:
        if speakers is not None and not speakers.done():
            speakers.cancel()
        await transcriber.disconnect()

    for note in notes:
        logger.warning(note)
    combined = TranscriptionResult()
    combined.finalize(text=text)
    combined.words = words
    combined.notes = notes
    return combined


async def _diarize_file(transcriber: BaseTranscriber, wav_chunks: list[bytes]) -> tuple[list[SpeakerTurn], str]:
    """Speaker turns for the whole file, or no turns and a note saying why there are none.

    The whole file goes to the server in one request, so speaker numbers stay the same across
    the 60 s transcription chunks. The timeout starts once this app's previous request is done.
    """
    while not _DIARIZE_LOCK.acquire(blocking=False):  # polled, so a cancelled task never holds it
        await asyncio.sleep(0.2)
    try:
        wav, duration_s = _whole_file_wav(wav_chunks)
        timeout = diarization_timeout(duration_s)
        try:
            turns = await transcriber.diarize(wav, timeout=timeout)
        except Exception as exc:
            import httpx

            reason = f"timed out after {timeout:.0f} s" if isinstance(exc, httpx.TimeoutException) else str(exc)
            reason = reason or type(exc).__name__
            return [], f"Speaker detection failed ({reason}); {NO_SPEAKER_LABELS}"
    finally:
        _DIARIZE_LOCK.release()
    if not turns:
        return [], f"Speaker detection found no speakers; {NO_SPEAKER_LABELS}"
    return turns, ""


def _whole_file_wav(wav_chunks: list[bytes]) -> tuple[io.BytesIO, float]:
    """The chunks as one WAV file (written chunk by chunk, no second copy of the PCM) and its length in seconds."""
    buf = io.BytesIO()
    frames = 0
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        for chunk in wav_chunks:
            with wave.open(io.BytesIO(chunk), "rb") as wf:
                frames += wf.getnframes()
                out.writeframes(wf.readframes(wf.getnframes()))
    buf.seek(0)
    return buf, frames / 16_000


def _unavailable_note(entry: dict, transcriber: BaseTranscriber) -> str:
    """Why an upload that asked for speakers got none, naming the models that can detect them."""
    if entry.get("diarization"):
        reason = getattr(transcriber, "no_diarization_reason", "")
        if reason:
            return f"{reason}; {NO_SPEAKER_LABELS}"
        return f"Speaker detection is not available with this server; {NO_SPEAKER_LABELS}"
    names = " or ".join(speaker_detection_models()) or "a model with speaker detection"
    return f"Speaker detection needs {names} as the active model; {NO_SPEAKER_LABELS}"


def _get_extension(filename: str) -> str:
    """Return the lowercase file extension including the dot."""
    dot = filename.rfind(".")
    if dot == -1:
        return ""
    return filename[dot:].lower()
