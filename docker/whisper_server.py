"""Minimal OpenAI-compatible transcription server for Whisper Large V3 Turbo via faster-whisper."""

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel

app = FastAPI()

model = None


@app.on_event("startup")
def load_model() -> None:
    global model  # noqa: PLW0603
    model = WhisperModel(
        "deepdml/faster-whisper-large-v3-turbo-ct2",
        device="cuda",
        compute_type="float16",
    )


@app.get("/health")
def health() -> dict:
    if model is None:
        return JSONResponse(status_code=503, content={"status": "loading"})
    return {"status": "ok"}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="deepdml/faster-whisper-large-v3-turbo-ct2"),
) -> dict:
    audio_bytes = await file.read()

    suffix = Path(file.filename).suffix if file.filename else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, _info = globals()["model"].transcribe(tmp_path, beam_size=1)
        text = " ".join(segment.text.strip() for segment in segments)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"text": text}
