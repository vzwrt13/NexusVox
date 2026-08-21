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
) -> dict:
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

        result = globals()["model"].transcribe([tmp_path])
        text = result[0].text if hasattr(result[0], "text") else str(result[0])
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {"text": text}
