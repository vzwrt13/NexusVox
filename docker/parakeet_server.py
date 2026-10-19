"""Minimal OpenAI-compatible transcription server for NVIDIA Parakeet TDT 0.6B v3."""

import tempfile
from pathlib import Path

import nemo.collections.asr as nemo_asr
import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

model = None


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
        # Ensure audio is 16kHz mono for optimal results
        data, sr = sf.read(tmp_path)
        if sr != 16000:
            import librosa

            data = librosa.resample(data, orig_sr=sr, target_sr=16000)
            sf.write(tmp_path, data, 16000)

        # Any non-None `timestamps` makes NeMo rebuild its decoding strategy, so dictation passes None.
        result = globals()["model"].transcribe([tmp_path], timestamps=True if want_words else None)
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
        words = _words(result[0]) if want_words else None
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if words is None:
        return {"text": text}
    return {"text": text, "words": words}


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
