"""Configuration loading from TOML file."""

from __future__ import annotations

import shutil
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")
EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.example.toml"

MODEL_REGISTRY: dict[str, dict[str, object]] = {
    "voxtral-mini-4b": {
        "hf_name": "mistralai/Voxtral-Mini-4B-Realtime-2602",
        "default_url": "ws://localhost:8000/v1/realtime",
        "protocol": "realtime_ws",
        "display_name": "Voxtral Mini 4B",
        "docker_profile": "voxtral",
        "health_url": "http://localhost:8000/health",
        "description": "Mistral real-time STT with causal audio encoder — <500 ms latency",
        "parameters": "4B (3.4B LM + 970M encoder)",
        "architecture": "Causal audio encoder + Mistral LM",
        "languages": "13 languages (AR, DE, EN, ES, FR, HI, IT, NL, PT, ZH, JA, KO, RU)",
        "streaming": "true",
        "vram_gb": "≥16 GB",
        "requires_gpu": True,
        "inprocess_supported": False,
    },
    "cohere-transcribe": {
        "hf_name": "CohereLabs/cohere-transcribe-03-2026",
        "default_url": "http://localhost:8001/v1/audio/transcriptions",
        "protocol": "openai_http",
        "display_name": "Cohere Transcribe",
        "docker_profile": "cohere",
        "health_url": "http://localhost:8001/health",
        "description": "2B Conformer-based STT — >3x real-time factor, 14 languages",
        "parameters": "2B",
        "architecture": "Conformer encoder + Transformer decoder",
        "languages": "14 languages (EN, FR, DE, IT, ES, PT, EL, NL, PL, ZH, JA, KO, VI, AR)",
        "streaming": "false",
        "vram_gb": "~6 GB",
        "requires_gpu": True,
        "inprocess_supported": False,
    },
    "parakeet-tdt-0.6b": {
        "hf_name": "nvidia/parakeet-tdt-0.6b-v3",
        "default_url": "http://localhost:8002/v1/audio/transcriptions",
        "protocol": "openai_http",
        "display_name": "Parakeet TDT 0.6B",
        "docker_profile": "parakeet",
        "health_url": "http://localhost:8002/health",
        "description": "NVIDIA FastConformer-TDT — 25 European languages with auto language detection",
        "parameters": "600M",
        "architecture": "FastConformer-TDT",
        "languages": "25 European languages with auto-detection",
        "streaming": "false",
        "vram_gb": "~2 GB",
        "requires_gpu": True,
        "inprocess_supported": False,
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
        "requires_gpu": False,
        "inprocess_supported": True,
    },
    "whisper-small": {
        "hf_name": "Systran/faster-whisper-small",
        "default_url": "",
        "protocol": "openai_http",
        "display_name": "Whisper Small (CPU-optimized)",
        "docker_profile": "",
        "health_url": "",
        "description": "OpenAI Whisper Small via faster-whisper — ~150 MB, fast on CPU, English-strong",
        "parameters": "244M",
        "architecture": "Transformer encoder-decoder (CTranslate2)",
        "languages": "99 languages",
        "streaming": "false",
        "vram_gb": "CPU (~500 MB RAM)",
        "requires_gpu": False,
        "inprocess_supported": True,
    },
    "distil-whisper-large-v3": {
        "hf_name": "Systran/faster-distil-whisper-large-v3",
        "default_url": "",
        "protocol": "openai_http",
        "display_name": "Distil-Whisper Large V3 (EN, CPU)",
        "docker_profile": "",
        "health_url": "",
        "description": "Distilled Whisper Large V3 — ~6x faster than large-v3 on CPU, English-only",
        "parameters": "756M",
        "architecture": "Transformer encoder-decoder (CTranslate2, distilled)",
        "languages": "English only",
        "streaming": "false",
        "vram_gb": "CPU (~1.5 GB RAM)",
        "requires_gpu": False,
        "inprocess_supported": True,
    },
    "distil-whisper-medium-en": {
        "hf_name": "Systran/faster-distil-whisper-medium.en",
        "default_url": "",
        "protocol": "openai_http",
        "display_name": "Distil-Whisper Medium.en (EN, CPU)",
        "docker_profile": "",
        "health_url": "",
        "description": "Distilled Whisper Medium.en — ~4x faster than medium on CPU, English-only",
        "parameters": "394M",
        "architecture": "Transformer encoder-decoder (CTranslate2, distilled)",
        "languages": "English only",
        "streaming": "false",
        "vram_gb": "CPU (~800 MB RAM)",
        "requires_gpu": False,
        "inprocess_supported": True,
    },
    "whisper-medium": {
        "hf_name": "Systran/faster-whisper-medium",
        "default_url": "",
        "protocol": "openai_http",
        "display_name": "Whisper Medium (CPU)",
        "docker_profile": "",
        "health_url": "",
        "description": "Whisper Medium via faster-whisper — multilingual incl. DE, ~3x faster than large-v3-turbo",
        "parameters": "769M",
        "architecture": "Transformer encoder-decoder (CTranslate2)",
        "languages": "99 languages",
        "streaming": "false",
        "vram_gb": "CPU (~1.5 GB RAM)",
        "requires_gpu": False,
        "inprocess_supported": True,
    },
    "whisper-base": {
        "hf_name": "Systran/faster-whisper-base",
        "default_url": "",
        "protocol": "openai_http",
        "display_name": "Whisper Base (CPU)",
        "docker_profile": "",
        "health_url": "",
        "description": "Whisper Base via faster-whisper — multilingual, smallest multilingual option, fastest",
        "parameters": "74M",
        "architecture": "Transformer encoder-decoder (CTranslate2)",
        "languages": "99 languages",
        "streaming": "false",
        "vram_gb": "CPU (~300 MB RAM)",
        "requires_gpu": False,
        "inprocess_supported": True,
    },
}


@dataclass
class HotkeyConfig:
    modifiers: list[str] = field(default_factory=lambda: ["ctrl", "shift", "alt"])


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    chunk_size: int = 4096


@dataclass
class InferenceConfig:
    server_url: str = "ws://localhost:8000/v1/realtime"
    transcription_delay_ms: int = 480
    model: str = "voxtral-mini-4b"
    device: str = "auto"
    compute_type: str | None = None


def resolve_device(requested: str) -> str:
    """Resolve 'auto'|'cuda'|'cpu' to a concrete 'cuda' or 'cpu'.

    Uses CTranslate2's runtime probe so we don't pull in torch just for detection.
    Any failure (missing driver, broken CUDA runtime, import error) falls back to CPU.
    """
    if requested in ("cpu", "cuda"):
        return requested
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def resolve_compute_type(device: str, override: str | None) -> str:
    if override:
        return override
    return "int8" if device == "cpu" else "float16"


@dataclass
class DatabaseConfig:
    path: str = "nexusvox.db"
    audio_dir: str = "audio"


@dataclass
class OSCommandsConfig:
    enabled: bool = False
    apps: dict[str, str] = field(default_factory=dict)


_DEFAULT_SYMBOLS: list[str] = [
    "slash",
    "backslash",
    "pipe",
    "tilde",
    "asterisk",
    "open paren",
    "close paren",
    "open bracket",
    "close bracket",
    "open brace",
    "close brace",
    "less than",
    "greater than",
]


@dataclass
class VoiceCommandsConfig:
    enabled: bool = True
    symbols: list[str] = field(default_factory=lambda: list(_DEFAULT_SYMBOLS))
    numbers_as_digits: bool = False
    bypass_symbols: bool = False


@dataclass
class Config:
    language: str = "en"
    injection_delay_ms: int = 500
    auto_language_detection: bool = False
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    os_commands: OSCommandsConfig = field(default_factory=OSCommandsConfig)
    voice_commands: VoiceCommandsConfig = field(default_factory=VoiceCommandsConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load configuration from a TOML file. Creates from example if missing."""
    if not path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            shutil.copy(EXAMPLE_CONFIG_PATH, path)
        else:
            return Config()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    general = data.get("general", {})
    hotkey_data = data.get("hotkey", {})
    audio_data = data.get("audio", {})
    inference_data = data.get("inference", {})
    db_data = data.get("database", {})
    os_cmd_data = data.get("os_commands", {})
    vc_data = data.get("voice_commands", None)

    # Backward compat: old configs use general.voice_commands_enabled (bool only)
    if vc_data is not None:
        vc_config = VoiceCommandsConfig(
            enabled=vc_data.get("enabled", True),
            symbols=list(vc_data.get("symbols", _DEFAULT_SYMBOLS)),
            numbers_as_digits=vc_data.get("numbers_as_digits", False),
            bypass_symbols=vc_data.get("bypass_symbols", False),
        )
    else:
        vc_config = VoiceCommandsConfig(
            enabled=general.get("voice_commands_enabled", True),
            symbols=list(_DEFAULT_SYMBOLS),
            numbers_as_digits=False,
        )

    return Config(
        language=general.get("language", "en"),
        injection_delay_ms=general.get("injection_delay_ms", 500),
        auto_language_detection=general.get("auto_language_detection", False),
        hotkey=HotkeyConfig(
            modifiers=hotkey_data.get("modifiers", ["ctrl", "shift", "alt"]),
        ),
        audio=AudioConfig(
            sample_rate=audio_data.get("sample_rate", 16_000),
            chunk_size=audio_data.get("chunk_size", 4096),
        ),
        inference=InferenceConfig(
            server_url=inference_data.get("server_url", "ws://localhost:8000/v1/realtime"),
            transcription_delay_ms=inference_data.get("transcription_delay_ms", 480),
            model=inference_data.get("model", "voxtral-mini-4b"),
            device=inference_data.get("device", "auto"),
            compute_type=inference_data.get("compute_type", None),
        ),
        database=DatabaseConfig(
            path=db_data.get("path", "nexusvox.db"),
            audio_dir=db_data.get("audio_dir", "audio"),
        ),
        os_commands=OSCommandsConfig(
            enabled=os_cmd_data.get("enabled", False),
            apps=dict(os_cmd_data.get("apps", {})),
        ),
        voice_commands=vc_config,
    )


_config_lock = threading.Lock()


def save_config(config: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Write the current configuration back to TOML."""
    with _config_lock:
        symbols_toml = "[{}]".format(", ".join(f'"{s}"' for s in config.voice_commands.symbols))
        lines = [
            "[general]",
            f'language = "{config.language}"',
            f"injection_delay_ms = {config.injection_delay_ms}",
            f"auto_language_detection = {'true' if config.auto_language_detection else 'false'}",
            "",
            "[hotkey]",
            "modifiers = [{}]".format(", ".join(f'"{m}"' for m in config.hotkey.modifiers)),
            "",
            "[audio]",
            f"sample_rate = {config.audio.sample_rate}",
            f"chunk_size = {config.audio.chunk_size}",
            "",
            "[inference]",
            f'server_url = "{config.inference.server_url}"',
            f"transcription_delay_ms = {config.inference.transcription_delay_ms}",
            f'model = "{config.inference.model}"',
            f'device = "{config.inference.device}"',
            *([f'compute_type = "{config.inference.compute_type}"'] if config.inference.compute_type else []),
            "",
            "[database]",
            f'path = "{config.database.path}"',
            f'audio_dir = "{config.database.audio_dir}"',
            "",
            "[os_commands]",
            f"enabled = {'true' if config.os_commands.enabled else 'false'}",
            "",
            "[os_commands.apps]",
            *[f'{name} = "{exe}"' for name, exe in config.os_commands.apps.items()],
            "",
            "[voice_commands]",
            f"enabled = {'true' if config.voice_commands.enabled else 'false'}",
            f"numbers_as_digits = {'true' if config.voice_commands.numbers_as_digits else 'false'}",
            f"bypass_symbols = {'true' if config.voice_commands.bypass_symbols else 'false'}",
            f"symbols = {symbols_toml}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
