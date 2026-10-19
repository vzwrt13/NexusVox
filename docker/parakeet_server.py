"""Minimal OpenAI-compatible transcription server for NVIDIA Parakeet TDT 0.6B v3.

Also serves speaker diarization for file uploads (`/v1/audio/diarization`, NVIDIA Streaming
Sortformer, at most 4 speakers). That model is loaded on the first diarization request only.
"""

import tempfile
import threading
from pathlib import Path

import nemo.collections.asr as nemo_asr
import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

model = None

DIARIZATION_MODEL = "nvidia/diar_streaming_sortformer_4spk-v2"
diar_model = None
_diar_lock = threading.Lock()


@app.on_event("startup")
def load_model() -> None:
    global model  # noqa: PLW0603
    model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")


@app.get("/health")
def health() -> dict:
    if model is None:
        return JSONResponse(status_code=503, content={"status": "loading"})
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="nvidia/parakeet-tdt-0.6b-v3"),
    response_format: str = Form(default="json"),
    timestamp_granularities: list[str] = Form(default=[], alias="timestamp_granularities[]"),
) -> dict:
    # Word timestamps only on the OpenAI-style request (verbose_json + word granularity);
    # every other request gets the plain {"text": ...} reply.
    want_words = response_format == "verbose_json" and "word" in timestamp_granularities
    audio_bytes = await file.read()

    # Write to a temp file — NeMo transcribe() expects file paths
    suffix = Path(file.filename).suffix if file.filename else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        _ensure_16k(tmp_path)

        # Any non-None `timestamps` makes NeMo rebuild its decoding strategy, so dictation passes None.
        result = globals()["model"].transcribe([tmp_path], timestamps=True if want_words else None)
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        words = _words(result[0]) if want_words else None
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if words is None:
        return {"text": text}
    return {"text": text, "words": words}


@app.post("/v1/audio/diarization")
def diarize(file: UploadFile = File(...)) -> dict:
    """Speaker turns for a whole file: {"segments": [{"speaker", "start", "end"}, ...]}, seconds.

    Runs once over the complete recording, so a speaker keeps one index for the whole file.
    A sync handler, so FastAPI runs it in its thread pool and dictation requests keep flowing.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name
    try:
        _ensure_16k(tmp_path)
        with _diar_lock:  # one load, and one long run at a time on the GPU
            lines = _load_diar_model().diarize(audio=[tmp_path], batch_size=1, verbose=False)[0]
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"segments": _segments(lines)}


def _load_diar_model():
    global diar_model  # noqa: PLW0603
    if diar_model is None:
        from nemo.collections.asr.models import SortformerEncLabelModel

        loaded = SortformerEncLabelModel.from_pretrained(DIARIZATION_MODEL)
        loaded.eval()
        # The model card's "very high latency" preset (in 80 ms frames): the largest chunks give the
        # best accuracy, and a file upload has no latency to keep low. The speaker cache keeps the
        # speaker order stable across chunks, so long files work in one pass.
        modules = loaded.sortformer_modules
        modules.chunk_len = 340
        modules.chunk_right_context = 40
        modules.fifo_len = 40
        modules.spkcache_update_period = 300
        modules.spkcache_len = 188
        modules._check_streaming_parameters()
        diar_model = loaded
    return diar_model


def _segments(lines) -> list[dict]:
    """Parse NeMo's "<start> <end> speaker_<n>" lines; anything else is skipped."""
    segments = []
    for line in lines:
        try:
            start, end, speaker = str(line).split()
            segments.append({"speaker": int(speaker.rsplit("_", 1)[-1]), "start": float(start), "end": float(end)})
        except ValueError:
            continue
    return segments


def _ensure_16k(path: str) -> None:
    """Resample the file in place to 16 kHz, the rate both models expect."""
    data, sr = sf.read(path)
    if sr != 16000:
        import librosa

        data = librosa.resample(data, orig_sr=sr, target_sr=16000)
        sf.write(path, data, 16000)


def _words(hypothesis) -> list[dict]:
    """Per-word {word, start, end} (seconds) from a NeMo hypothesis, OpenAI verbose_json style.

    An entry without usable timing is skipped, so a NeMo version with another shape costs the
    word list, never the transcript.
    """
    timestamp = getattr(hypothesis, "timestamp", None)
    entries = timestamp.get("word") if isinstance(timestamp, dict) else None
    words = []
    for entry in entries or []:
        try:
            words.append({"word": entry["word"], "start": float(entry["start"]), "end": float(entry["end"])})
        except (KeyError, TypeError, ValueError):
            continue
    return words
