"""Audio file conversion and transcription for uploaded files."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import threading
import wave
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from ..config import MODEL_REGISTRY, InferenceConfig
from .diarization import diarization_timeout, format_transcript, label_words, speaker_detection_models
from .transcriber import BaseTranscriber, SpeakerTurn, TranscriptionResult, Word, create_transcriber, pcm16_to_wav_bytes

logger = logging.getLogger("nexusvox.file_transcribe")

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".webm"}
# An upload is streamed to disk and converted window by window, so the limit is about disk space, not memory.
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_UPLOAD_LABEL = f"{MAX_UPLOAD_BYTES // 1024**3} GB"  # how the limit is named in messages
TARGET_RATE = 16_000  # what the transcription servers take: 16 kHz mono PCM16
# Prefix of this app's temporary upload and conversion files; the start-up sweep deletes what a crash left.
TEMP_PREFIX = "nexusvox-"
MAX_CHUNK_MS = 60_000  # 60 seconds per chunk — safe for 16 GB VRAM
# Speaker detection sends the whole file to the server in one request and holds it once more as one WAV (about
# 115 MB per hour on top of the chunks), so a longer recording skips it.
MAX_DETECTION_S = 2 * 3600.0
_SEND_CHUNK_BYTES = 4096
# Ends every note that says why speaker detection gave nothing; a meeting swaps it for what its transcript does.
NO_SPEAKER_LABELS = "the transcript has no speaker labels."
# One request at a time from this app, chunk transcription and speaker detection alike, across every job
# (each upload and meeting runs in its own thread): the server runs them one after the other on one GPU
# anyway, and a request's timeout must not include the time it waits behind another job's run.
_SERVER_LOCK = threading.Lock()


@asynccontextmanager
async def _server_turn() -> AsyncIterator[None]:
    """Hold the process-wide server slot. Polled, so a cancelled task never holds it."""
    while not _SERVER_LOCK.acquire(blocking=False):
        await asyncio.sleep(0.2)
    try:
        yield
    finally:
        _SERVER_LOCK.release()


def detection_too_long_note() -> str:
    """The note for a recording over `MAX_DETECTION_S`: speaker detection is not attempted."""
    return (
        f"Speaker detection skipped: the recording is longer than {MAX_DETECTION_S / 3600:g} hours; {NO_SPEAKER_LABELS}"
    )


def file_extension(filename: str) -> str:
    """The lowercase extension with its dot; a name that is only an extension (".mp3") counts as that extension."""
    return Path("x" + filename).suffix.lower()


class WavChunks:
    """A converted file: 16 kHz mono PCM16 WAV on disk, handed out as WAV chunks of `MAX_CHUNK_MS`, one at a time.

    Behaves like a list of `bytes` (length, indexing, iteration, repeatable) but holds only the chunk being read,
    so memory does not grow with the length of the audio. No file handle stays open between chunks. Closing (or
    leaving the `with` block) deletes the file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        with wave.open(str(path), "rb") as wf:
            self.frames = wf.getnframes()
        self._window = TARGET_RATE * MAX_CHUNK_MS // 1000

    @property
    def duration_ms(self) -> int:
        return self.frames * 1000 // TARGET_RATE

    def __len__(self) -> int:
        return -(-self.frames // self._window)

    def __getitem__(self, index: int) -> bytes:
        count = len(self)
        if not -count <= index < count:
            raise IndexError("chunk index out of range")
        with wave.open(str(self.path), "rb") as wf:
            wf.setpos((index % count) * self._window)
            return pcm16_to_wav_bytes(wf.readframes(self._window))

    def __iter__(self) -> Iterator[bytes]:
        for index in range(len(self)):
            yield self[index]

    def close(self) -> None:
        """Delete the file. One that is still held (a virus scanner or the search indexer on Windows) must not
        throw away a finished transcription, so a failed delete is logged and left to the start-up sweep."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete the temporary file %s: %s", self.path, exc)

    def __enter__(self) -> WavChunks:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def convert_to_wav(source: str | Path) -> WavChunks:
    """Decode an audio file on disk to a 16 kHz mono PCM16 WAV in a temporary file, packet by packet.

    PyAV (FFmpeg, bundled with the wheel: no ffmpeg on PATH) decodes any supported format and resamples it with
    FFmpeg's filtered resampler, so a WAV and an MP3 of the same recording come out alike. Memory stays at about one
    decoded packet, whatever the length. The result serves the file as chunks of `MAX_CHUNK_MS` (to avoid GPU OOM);
    the caller closes it, which deletes the temporary file. FFmpeg recognises the format from the content, not
    from a file name.
    """
    source = Path(source)
    fd, name = tempfile.mkstemp(prefix=TEMP_PREFIX + "convert-", suffix=".wav")
    os.close(fd)
    out = Path(name)
    try:
        _decode_to_wav(source, out)
        audio = WavChunks(out)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    logger.info("Split %d ms audio into %d chunk(s)", audio.duration_ms, len(audio))
    return audio


def _decode_to_wav(source: Path, out: Path) -> None:
    """Write `source` to `out` as 16 kHz mono PCM16 WAV; ValueError when it cannot be read as audio."""
    import av

    try:
        container = av.open(str(source))
    except av.error.FFmpegError as exc:
        raise ValueError(f"could not read the file as audio: {exc}") from exc
    with container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("the file has no audio stream")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
        skipped = 0
        try:
            with wave.open(str(out), "wb") as dst:
                dst.setnchannels(1)
                dst.setsampwidth(2)
                dst.setframerate(TARGET_RATE)
                for packet in container.demux(stream):
                    try:
                        frames = packet.decode()
                    except av.error.InvalidDataError:  # one damaged packet costs its own audio, not the file
                        skipped += 1
                        continue
                    for frame in frames:
                        for resampled in resampler.resample(frame):
                            dst.writeframes(resampled.to_ndarray().tobytes())
                for resampled in resampler.resample(None):  # what the resampler still holds
                    dst.writeframes(resampled.to_ndarray().tobytes())
        except av.error.FFmpegError as exc:
            raise ValueError(f"could not decode the audio: {exc}") from exc
    if skipped:
        logger.warning("Skipped %d damaged packet(s) while decoding %s", skipped, source.name)


async def transcribe_file(
    wav_chunks: Sequence[bytes],
    config: InferenceConfig,
    *,
    device: str | None = None,
    word_timestamps: bool = False,
    diarize: bool = False,
    on_progress: Callable[..., None] | None = None,
    explain_no_speakers: bool = True,
) -> TranscriptionResult:
    """Transcribe WAV chunks via the shared transcriber factory and join results.

    `wav_chunks` is a list of WAV `bytes`, or a `WavChunks` that reads them from disk one at a time.

    With `word_timestamps`, engines that support it also return per-word timing, shifted to
    the file's timeline in the joined result. With `diarize` (implies word timestamps), engines
    that support it also detect speakers once over the whole file; the text then becomes
    "Speaker 1: ..." paragraphs and each word carries its speaker. When speaker detection is not
    possible or fails, the plain transcript comes back with a note saying why; a recording longer than
    `MAX_DETECTION_S` skips it with a note instead of sending one huge request.

    With `explain_no_speakers` False the note about missing speaker detection is left out (the caller has said why).

    `on_progress(stage, current, total)` is called as the work moves on: `"transcribing"` as chunk
    `current` of `total` starts, `"diarizing"` when only speaker detection is left.

    Dispatches through `create_transcriber` so CPU users (in-process Whisper)
    and GPU users (Docker-backed HTTP/WS) share one code path.
    """
    transcriber = create_transcriber(
        config,
        device=device,
        auto_fallback=config.device == "auto",
    )
    if diarize and hasattr(transcriber, "preload_cpu"):
        transcriber.preload_cpu = False  # this job waits for the GPU server: no 670 MB CPU model load for it
    # An engine that chooses between a server and the CPU settles on one before its flags are read.
    await transcriber.prepare()
    if diarize:
        # A reachable but cold GPU server is worth the wait (~17 s): the CPU engine cannot detect speakers.
        wait_until_warm = getattr(transcriber, "wait_until_warm", None)
        if wait_until_warm is not None:
            await wait_until_warm()
    # Both the model's registry entry and the client must support words; a custom server
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
    try:
        for i, wav_bytes in enumerate(wav_chunks):
            logger.info("Transcribing chunk %d/%d", i + 1, len(wav_chunks))
            if on_progress:
                on_progress("transcribing", i + 1, len(wav_chunks))
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                pcm_data = wf.readframes(wf.getnframes())

            async with _server_turn():  # the timeout starts once no other request of this app is running
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
        plain_text = text
        if diarize and not wants_speakers:
            if explain_no_speakers:
                notes.append(_unavailable_note(entry, transcriber))
        elif wants_speakers and text:
            if offset_s > MAX_DETECTION_S:
                notes.append(detection_too_long_note())
            elif not words_complete:
                # Labelled paragraphs are built from the words; a word without timing would vanish,
                # so the turns would be thrown away: do not ask the server for them.
                notes.append("Speaker detection skipped: some words came back without timing.")
            else:
                # Only now: the server runs transcription and speaker detection one after the other on
                # one GPU, so a request made alongside the chunks would eat into a chunk's timeout.
                if on_progress:
                    on_progress("diarizing")
                turns, problem = await _diarize_file(transcriber, wav_chunks)
                if problem:
                    notes.append(problem)
                else:
                    words = label_words(words, turns)
                    text = format_transcript(words)
    finally:
        await transcriber.disconnect()

    for note in notes:
        logger.warning(note)
    combined = TranscriptionResult()
    combined.finalize(text=text)
    combined.plain_text = plain_text
    combined.words = words
    combined.notes = notes
    return combined


async def _diarize_file(transcriber: BaseTranscriber, wav_chunks: Sequence[bytes]) -> tuple[list[SpeakerTurn], str]:
    """Speaker turns for the whole file, or no turns and a note saying why there are none.

    The whole file goes to the server in one request, so speaker numbers stay the same across
    the 60 s transcription chunks. The timeout starts once this app's previous request is done.
    """
    wav, duration_s = await asyncio.to_thread(_whole_file_wav, wav_chunks)
    timeout = diarization_timeout(duration_s)
    async with _server_turn():
        try:
            turns = await transcriber.diarize(wav, timeout=timeout)
        except Exception as exc:
            import httpx

            reason = f"timed out after {timeout:.0f} s" if isinstance(exc, httpx.TimeoutException) else str(exc)
            reason = reason or type(exc).__name__
            return [], f"Speaker detection failed ({reason}); {NO_SPEAKER_LABELS}"
    if not turns:
        return [], f"Speaker detection found no speakers; {NO_SPEAKER_LABELS}"
    return turns, ""


def _whole_file_wav(wav_chunks: Sequence[bytes]) -> tuple[io.BytesIO, float]:
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
