"""`[inference]`: the model registry, the Docker decision and the transcription server settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MODEL_REGISTRY: dict[str, dict[str, object]] = {
    "parakeet-tdt-0.6b": {
        "hf_name": "nvidia/parakeet-tdt-0.6b-v3",
        "default_url": "http://localhost:8002/v1/audio/transcriptions",
        "protocol": "openai_http",
        "display_name": "Parakeet TDT 0.6B",
        "docker_profile": "parakeet",
        "word_timestamps": True,
        # Speaker detection for file uploads (the container's /v1/audio/diarization endpoint).
        "diarization": True,
        "health_url": "http://localhost:8002/health",
        "description": "NVIDIA FastConformer-TDT — 25 European languages with auto language detection",
        "parameters": "600M",
        "architecture": "FastConformer-TDT",
        "languages": "25 European languages with auto-detection",
        "streaming": "false",
        "vram_gb": "GPU ~2 GB · CPU ~670 MB download",
        # GPU: the Docker NeMo container. CPU: in-process ONNX int8 through onnx-asr (no Docker).
        "inprocess_supported": True,
        "inprocess_engine": "parakeet-onnx",
        "onnx_model": "nemo-parakeet-tdt-0.6b-v3",
        "onnx_quantization": "int8",
        # The in-process copy the dashboard marks as downloaded (Hugging Face repo, a file that proves it, size).
        "download_repo": "istupakov/parakeet-tdt-0.6b-v3-onnx",
        "download_probe": "encoder-model.int8.onnx",
        "download_size": "~670 MB",
    },
    "whisper-large-v3-turbo": {
        "hf_name": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "default_url": "http://localhost:8003/v1/audio/transcriptions",
        "protocol": "openai_http",
        "display_name": "Whisper Large V3 Turbo",
        "docker_profile": "whisper",
        "health_url": "http://localhost:8003/health",
        "description": "OpenAI Whisper Large V3 Turbo via faster-whisper — CTranslate2 optimized, 99 languages",
        "parameters": "809M",
        "architecture": "Transformer encoder-decoder (CTranslate2)",
        "languages": "99 languages",
        "streaming": "false",
        "vram_gb": "~4 GB",
        "inprocess_supported": True,
        "download_probe": "model.bin",
        "download_size": "~1.6 GB",
    },
    "custom-server": {
        # Not a model but a slot: any server the user runs, at [inference.custom].url,
        # speaking one of the two wire protocols above. Never started through Docker.
        "hf_name": "",
        "default_url": "",
        "protocol": "custom",
        "display_name": "Custom server",
        "docker_profile": "",
        "health_url": "",
        "description": "Your own server at [inference.custom] in config.toml — "
        "audio leaves this machine if that URL is remote",
        "parameters": "—",
        "architecture": "OpenAI-compatible HTTP or realtime WebSocket",
        "languages": "Depends on the server",
        "streaming": "false",
        "vram_gb": "None locally",
        "inprocess_supported": False,
        "custom": True,
    },
}

# The model every config falls back to: one that names a model no longer in the registry, and a
# model that cannot start on this machine.
DEFAULT_MODEL = "parakeet-tdt-0.6b"


# Wire protocols a custom server can speak, mapped to the registry protocol they reuse.
CUSTOM_PROTOCOLS = {"http": "openai_http", "realtime": "realtime_ws"}


def model_download_state(model_id: str, cache_dir: str | Path | None = None) -> dict[str, object]:
    """Whether the in-process copy of `model_id` is already on disk, and how big the download is.

    `downloaded` is None for a model without an in-process copy (the custom server) and when the cache
    cannot be read; `size` is empty then. `cache_dir` is the installed app's models folder (None from
    source: the Hugging Face cache).
    """
    info = MODEL_REGISTRY.get(model_id, {})
    # The repo defaults to the model's own Hugging Face name; only the ONNX Parakeet build lives elsewhere.
    repo, probe = info.get("download_repo") or info.get("hf_name"), info.get("download_probe")
    if not repo or not probe:
        return {"downloaded": None, "size": ""}
    try:
        from huggingface_hub import try_to_load_from_cache

        found = try_to_load_from_cache(str(repo), str(probe), cache_dir=cache_dir)
        downloaded: bool | None = isinstance(found, str)
    except Exception:
        downloaded = None
    return {"downloaded": downloaded, "size": str(info.get("download_size", ""))}


def model_needs_docker(model_id: str, device: str) -> bool:
    """Whether `model_id` runs in a Docker container on the resolved `device`."""
    info = MODEL_REGISTRY.get(model_id, {})
    if info.get("custom"):
        return False
    return not (info.get("inprocess_supported") and device == "cpu")


@dataclass
class CustomServerConfig:
    """A user-run transcription server, used when the model is "custom-server"."""

    url: str = ""
    # "http": WAV POSTed after recording stops (OpenAI /v1/audio/transcriptions).
    # "realtime": audio streamed while recording (vLLM /v1/realtime WebSocket).
    protocol: str = "http"
    # Sent to the server as the model field, and stored with each transcript.
    model_name: str = "custom-server"


@dataclass
class InferenceConfig:
    server_url: str = "http://localhost:8002/v1/audio/transcriptions"
    transcription_delay_ms: int = 480
    model: str = "parakeet-tdt-0.6b"
    device: str = "auto"
    compute_type: str | None = None
    custom: CustomServerConfig = field(default_factory=CustomServerConfig)
