"""Configuration loading from TOML file."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import default_config_path, resolve_data_path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.toml")
# Resolved next to this module, not relative to the repository root. The template
# ships inside the package (see [tool.setuptools.package-data]), so an installed
# wheel finds it in site-packages exactly as a source checkout finds it in src/.
# A path walking up to the repository root works only in a checkout and silently
# leaves wheel users without a config.toml at all.
EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parent / "config.example.toml"

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

# Default URLs of the removed Voxtral and Cohere containers.
_REMOVED_MODEL_URLS = {"ws://localhost:8000/v1/realtime", "http://localhost:8001/v1/audio/transcriptions"}
_WARNED_REMOVED_MODELS: set[str] = set()

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


# Default bindings, the single source for the dataclasses, the loader and the fallbacks. The legacy ones are what
# a config file written before the current defaults meant by a section that leaves the modifiers out.
DEFAULT_HOTKEY_MODIFIERS = ("ctrl", "win")
LEGACY_HOTKEY_MODIFIERS = ("ctrl", "shift", "alt")
DEFAULT_MEETING_MODIFIERS = ("ctrl", "shift")
LEGACY_MEETING_MODIFIERS = ("ctrl", "alt")


@dataclass
class HotkeyConfig:
    modifiers: list[str] = field(default_factory=lambda: list(DEFAULT_HOTKEY_MODIFIERS))
    # Optional non-modifier key that completes the combo (e.g. "a", "f9", "space").
    # Empty: the modifiers alone are the hotkey. Its keystrokes are swallowed while
    # it is held as part of the combo, so the target app never sees the letter.
    key: str = ""
    # "hold": record while the modifiers are held (push-to-talk).
    # "toggle": press once to start recording, press again to stop.
    mode: str = "hold"
    # Pressed during a recording with every modifier down: the recording is dropped and
    # the last pasted text is pasted again (Ctrl+Win+V). Empty turns it off.
    paste_key: str = "V"
    # Pressed during a recording with every modifier down: the selected text is rewritten by
    # the spoken instruction through the [llm] model (Ctrl+Win+P). Empty: off.
    polish_key: str = ""


HOTKEY_MODES = ("hold", "toggle")
# `[general].log_level`: names of the standard logging levels a user may pick.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
HOTKEY_MODIFIERS = ("ctrl", "shift", "alt", "win")

# Non-modifier keys a hotkey may end with, name -> Win32 virtual-key code. Layout
# independent keys only: letters, digits, function keys and a few that never type.
HOTKEY_KEY_VKS: dict[str, int] = {
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    **{f"F{n}": 0x6F + n for n in range(1, 25)},
    "SPACE": 0x20,
    "CAPS_LOCK": 0x14,
    "SCROLL_LOCK": 0x91,
    "PAUSE": 0x13,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGE_UP": 0x21,
    "PAGE_DOWN": 0x22,
}


def normalize_hotkey_key(key: str) -> str | None:
    """Canonical (upper-case) key name, "" for none, None if unknown."""
    name = (key or "").strip().upper()
    if name == "":
        return ""
    return name if name in HOTKEY_KEY_VKS else None


def validate_hotkey(modifiers: list[str], key: str) -> str | None:
    """Error message for an unusable binding, None if it is fine."""
    if any(m not in HOTKEY_MODIFIERS for m in modifiers):
        return "Unknown modifier; use ctrl, shift, alt, win"
    if len(set(modifiers)) != len(modifiers):
        return "Duplicate modifier"
    if normalize_hotkey_key(key) is None:
        return f"Unknown key: {key}"
    if not modifiers and not key:
        return "A hotkey needs at least one modifier or a key"
    return None


MAX_SPEAKER_LABEL_CHARS = 40


def numbered_speaker_label(number: int) -> str:
    """The label a speaker has when nobody named them: "Speaker 1", "Speaker 2", ..."""
    return f"Speaker {number}"


DEFAULT_OWN_VOICE_LABEL = numbered_speaker_label(1)


def normalize_speaker_label(label: object) -> str:
    """A speaker label on one line (whitespace collapsed, at most 40 characters); the default when nothing is left."""
    text = " ".join(str(label or "").split())[:MAX_SPEAKER_LABEL_CHARS].strip()
    return text or DEFAULT_OWN_VOICE_LABEL


def own_voice_label_problem(label: str) -> str | None:
    """Why `label` cannot name the microphone's speaker, None when it can.

    "Speaker 2", "Speaker 3", ... are the labels the other participants get, so using one would write two
    people under one name.
    """
    folded = label.casefold()
    for number in range(2, 100):
        if folded == numbered_speaker_label(number).casefold():
            return f"{label!r} is the label of another speaker; pick a different name"
    return None


@dataclass
class MeetingConfig:
    """Meeting recording (microphone + system audio) started from the dashboard or its own hotkey.

    The hotkey is a toggle and always needs a key, so it never collides with the dictation hotkey; the
    default Ctrl+Shift+M leaves the default dictation chord (Ctrl+Win) untouched and, having no Alt, opens no
    app menu. Empty device names mean the system default devices.
    """

    modifiers: list[str] = field(default_factory=lambda: list(DEFAULT_MEETING_MODIFIERS))
    key: str = "M"
    mic_device: str = ""
    output_device: str = ""
    # Show "Inform all participants before you record" before each recording; off after "don't show again".
    consent_hint: bool = True
    # Drop the Me words spoken inside a dictation take from the meeting transcript (the microphone records
    # dictation too). Applied when the meeting is transcribed; the audio files stay as recorded.
    leave_dictation_out: bool = True
    # The label of the microphone's speaker in a meeting transcript; the other participants are numbered on from 2.
    own_voice_label: str = DEFAULT_OWN_VOICE_LABEL
    # Show a rough transcript of both tracks (your voice and "Others") in the dashboard while the meeting
    # records. It sends the audio to the transcription model in 6 s windows all meeting long, and that load is not
    # measured yet, so it is off until switched on (dashboard Meetings tab).
    live_transcript: bool = False
    # Keep `meeting.wav`, the playable mix, after a successful transcription. The per-track WAVs are always
    # deleted then; off deletes the mix too. Meetings already stored keep their audio.
    store_audio: bool = True


def meeting_hotkey_clash(modifiers: list[str], key: str, dictation: HotkeyConfig) -> str | None:
    """Why a meeting chord and the dictation hotkey cannot both exist, None when they can.

    The dictation listener fires on any chord that holds at least its modifiers (and its key), so a meeting
    chord that contains the dictation chord starts dictation as well.
    """
    chord, dictation_modifiers = set(modifiers), set(dictation.modifiers)
    if not dictation_modifiers <= chord:
        return None
    if dictation.key and normalize_hotkey_key(key) != dictation.key:
        return None
    if chord == dictation_modifiers and normalize_hotkey_key(key) == dictation.key:
        return "That is the dictation hotkey"
    return "That chord would also start dictation; change its modifiers or its key"


def validate_meeting_hotkey(modifiers: list[str], key: str, dictation: HotkeyConfig) -> str | None:
    """Error message for a meeting binding that is unusable or would also trigger dictation."""
    error = validate_hotkey(modifiers, key)
    if error is not None:
        return error
    if not normalize_hotkey_key(key):
        return "The meeting hotkey needs a key"
    if not modifiers:
        return "The meeting hotkey needs at least one modifier"
    return meeting_hotkey_clash(modifiers, key, dictation)


# Tried in order when a hand-edited file leaves no usable meeting hotkey.
_MEETING_HOTKEY_FALLBACKS = (
    (list(DEFAULT_MEETING_MODIFIERS), "M"),
    (list(DEFAULT_MEETING_MODIFIERS), "F9"),
    (list(LEGACY_MEETING_MODIFIERS), "M"),
    (list(LEGACY_MEETING_MODIFIERS), "F9"),
    (["ctrl", "alt", "win"], "M"),
)


def default_meeting_hotkey(dictation: HotkeyConfig) -> tuple[list[str], str]:
    """The default meeting hotkey, or the first fallback that does not clash with the dictation hotkey."""
    for modifiers, key in _MEETING_HOTKEY_FALLBACKS:
        if validate_meeting_hotkey(modifiers, key, dictation) is None:
            return list(modifiers), key
    return ["ctrl", "alt", "win"], "F12"


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    chunk_size: int = 4096


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


def cuda_available() -> bool:
    """True when a CUDA GPU is usable: the gate for features that need the local language model."""
    try:
        return resolve_device("auto") == "cuda"
    except Exception:
        return False


_vram_probed = False
_vram_gb: float | None = None


def gpu_vram_gb() -> float | None:
    """Total VRAM of the first NVIDIA GPU in GB, or None without one (read once through nvidia-smi)."""
    global _vram_probed, _vram_gb
    if not _vram_probed:
        _vram_probed = True
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            _vram_gb = float(out.splitlines()[0].strip()) / 1024
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            _vram_gb = None
    return _vram_gb


# 12 GB and up hold the speech model and gemma4:e4b together (docs/llm-cpu-test.md).
LLM_E4B_MIN_VRAM_GB = 12.0


def llm_runs_fast() -> bool:
    """True with a CUDA GPU that has room for gemma4:e4b next to the speech model."""
    if not cuda_available():
        return False
    vram = gpu_vram_gb()
    return vram is not None and vram >= LLM_E4B_MIN_VRAM_GB - 0.5  # nvidia-smi reports 12 GB cards as ~11.9


def resolve_compute_type(device: str, override: str | None) -> str:
    if override:
        return override
    return "int8" if device == "cpu" else "float16"


@dataclass
class DatabaseConfig:
    path: str = "nexusvox.db"
    audio_dir: str = "audio"
    # Keep a WAV of every recording next to the database (the Review tab needs it).
    store_audio: bool = True


@dataclass
class OSCommandsConfig:
    enabled: bool = False
    apps: dict[str, str] = field(default_factory=dict)


@dataclass
class ExperimentalConfig:
    """Features that are finished in code but not part of v1; each is off (hidden and unreachable) by default.

    - ``os_commands``: Nexus OS commands (voice window management) and their Settings card.
    - ``flag_command``: the "nexus flag" voice command and the Edit tab ("Flagged Transcriptions").
    - ``github_snippets``: the Snippets button "Fill from GitHub" and its endpoint.
    - ``dev_tab``: the developer benchmark page of the dashboard.
    """

    os_commands: bool = False
    flag_command: bool = False
    github_snippets: bool = False
    dev_tab: bool = False


@dataclass
class AssistantConfig:
    """Where "nexus assistant <text>" is forwarded: a local TCP port that answers
    `send <service> <text>` (the personal-tooling Controller's control port)."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 49730
    service: str = "voice-assistant"
    timeout_s: float = 5.0


# Keys that can mark a recording as an os-control command, pressed as the last key of the
# hotkey chord (Ctrl+Win+Y by default). OEM_102 is the "<" key left of Y/Z on a
# German (ISO) keyboard.
COMMAND_KEY_VKS: dict[str, int] = {"Y": 0x59, "OEM_102": 0xE2}


@dataclass
class OsControlConfig:
    """Command recordings: pressing `command_key` while the voice hotkey is held sends
    that recording's transcript to a local TCP port as `send <service> <text>` instead of
    typing it (the personal-tooling Controller's os-control service)."""

    enabled: bool = False
    command_key: str = "Y"
    host: str = "127.0.0.1"
    port: int = 49730
    service: str = "os-control"
    timeout_s: float = 5.0


@dataclass
class TranslatorConfig:
    """Translate the text into English before injecting it, via a local HTTP
    translator (the personal-tooling `translator` tool). The stored transcript stays
    in the spoken language; only the injected text is translated."""

    enabled: bool = False
    url: str = "http://127.0.0.1:8003/translate"
    timeout_s: float = 8.0


@dataclass
class MicGuardConfig:
    """Mute every other app's microphone session (Discord, Teams, browser calls) while
    push-to-talk is held, and put each back the way it was on release. Per-session
    WASAPI mute, never the device. `watchdog_s` restores the microphones if no key-up
    is ever seen while the keys are no longer physically down."""

    enabled: bool = False
    watchdog_s: float = 30.0


FEEDBACK_MODES = ("sound", "visual", "both")


@dataclass
class FeedbackConfig:
    """How a dictation signals its state: the start/stop chimes (`sound`), a small waveform at the bottom
    centre of the screen (`visual`), or `both`. Read on every cue, so a change applies without a restart."""

    mode: str = "sound"


LLM_PROTOCOLS = ("ollama", "openai")
# Preselected when `[llm].model` is not set and there is no CUDA GPU (docs/llm-cpu-test.md).
LLM_MODEL_CPU = "gemma4:e2b"


@dataclass
class LlmConfig:
    """The language model server polish talks to. Not part of NexusVox: Ollama (default)
    or any OpenAI-compatible server. `keep_alive` (Ollama only) is how long the model
    stays loaded after a request; shorter frees the VRAM sooner, longer avoids reloads."""

    url: str = "http://127.0.0.1:11434"
    protocol: str = "ollama"
    model: str = "gemma4:e4b"  # without a CUDA GPU and with no choice made: LLM_MODEL_CPU
    api_key: str = ""
    timeout_s: float = 60.0
    keep_alive: str = "10m"


CLEANUP_LEVELS = ("off", "light", "medium")


@dataclass
class CleanupConfig:
    """Tidy every dictation through the [llm] model before it is typed: `light` drops
    fillers and fixes punctuation, `medium` also grammar. `timeout_s` is short on purpose:
    a model still loading must not hold the dictation up; it is typed unchanged instead."""

    level: str = "off"
    timeout_s: float = 3.0


# `[cleanup].timeout_s` when unset and there is no CUDA GPU: the CPU needs 2-6 s (docs/llm-cpu-test.md).
CLEANUP_TIMEOUT_CPU_S = 10.0


def auto_llm_model() -> str:
    """The model for an unset `[llm].model`: gemma4:e4b with a CUDA GPU of 12 GB or more, else gemma4:e2b."""
    return LlmConfig.model if llm_runs_fast() else LLM_MODEL_CPU


def auto_cleanup_timeout() -> float:
    """The cleanup timeout for an unset `[cleanup].timeout_s`: longer on the CPU."""
    return CleanupConfig.timeout_s if cuda_available() else CLEANUP_TIMEOUT_CPU_S


def with_hardware_defaults(config: Config) -> Config:
    """*config* with the hardware-dependent defaults applied (a config built without a file)."""
    config.llm.model = auto_llm_model()
    config.cleanup.timeout_s = auto_cleanup_timeout()
    return config


def _llm_model(data: dict) -> str:
    """The configured model, or the hardware default when none is set."""
    return str(data["model"]) if "model" in data else auto_llm_model()


def _cleanup_config(data: dict) -> CleanupConfig:
    """An unknown level is off; a timeout of 0 or less would fail every request at once."""
    level = str(data.get("level", "off")).strip().lower()
    timeout_s = float(data["timeout_s"]) if "timeout_s" in data else auto_cleanup_timeout()
    return CleanupConfig(
        level=level if level in CLEANUP_LEVELS else "off",
        timeout_s=timeout_s if timeout_s > 0 else CleanupConfig.timeout_s,
    )


@dataclass
class PolishConfig:
    """How polish reads the selection. In a terminal it needs Ctrl+C (the terminal does
    not report its selection), which interrupts the running program when nothing is
    selected, so terminals are skipped unless `in_terminals` is set."""

    in_terminals: bool = False


@dataclass
class LearnFromEditsConfig:
    """After a paste, read the focused text field through UI Automation for `window_s`
    seconds and turn the words the user corrects into dictionary suggestions, accepted
    or dismissed in the dashboard. Off by default: it reads another app's text field
    (never a password field)."""

    enabled: bool = False
    window_s: float = 20.0


@dataclass
class RemoteMicConfig:
    """Let a phone use NexusVox as its speech recogniser over a WebSocket: it streams
    audio, gets the processed text back and inserts it itself. Off by default; it needs a
    token, and `host` should be a private address (a Tailscale IP). See docs/REMOTE_MIC.md."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 47393
    token: str = ""


@dataclass
class SupportConfig:
    """`feedback_form_url`: the page the dashboard's "Send feedback" button opens in the browser
    (app version, Windows version and hardware tier are added as URL parameters). Empty (the
    default, until the form exists) hides the button."""

    feedback_form_url: str = ""


def _feedback_form_url(value: object) -> str:
    """The configured form URL; a value that is not an http(s) URL is logged and treated as unset."""
    url = value.strip() if isinstance(value, str) else ""
    if value and not url.lower().startswith(("http://", "https://")):
        logging.getLogger(__name__).warning(
            "[support].feedback_form_url must be an http(s) URL; the Send feedback button stays hidden"
        )
        return ""
    return url


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


_DEFAULT_STRUCTURAL = ["new paragraph", "new line", "tab", "all caps"]


@dataclass
class VoiceCommandsConfig:
    enabled: bool = True
    symbols: list[str] = field(default_factory=lambda: list(_DEFAULT_SYMBOLS))
    numbers_as_digits: bool = True
    bypass_symbols: bool = False
    # Structural commands (new line, new paragraph, tab, all caps): own category, own list, own bypass.
    structural: list[str] = field(default_factory=lambda: list(_DEFAULT_STRUCTURAL))
    bypass_structural: bool = False
    # Trailing "press enter" / "Enter drücken" presses Enter after the paste. Opt-in.
    press_enter: bool = False


@dataclass
class Config:
    injection_delay_ms: int = 500
    log_level: str = "INFO"
    # Dictated text goes into the log file in full; off shortens it there (the console always shows it).
    log_full_text: bool = False
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    meeting: MeetingConfig = field(default_factory=MeetingConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    os_commands: OSCommandsConfig = field(default_factory=OSCommandsConfig)
    experimental: ExperimentalConfig = field(default_factory=ExperimentalConfig)
    voice_commands: VoiceCommandsConfig = field(default_factory=VoiceCommandsConfig)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    os_control: OsControlConfig = field(default_factory=OsControlConfig)
    translator: TranslatorConfig = field(default_factory=TranslatorConfig)
    mic_guard: MicGuardConfig = field(default_factory=MicGuardConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    learn_from_edits: LearnFromEditsConfig = field(default_factory=LearnFromEditsConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    polish: PolishConfig = field(default_factory=PolishConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    remote_mic: RemoteMicConfig = field(default_factory=RemoteMicConfig)
    support: SupportConfig = field(default_factory=SupportConfig)
    # The file `load_config` read, as an absolute path; `save_config` writes back to it, whatever the working
    # directory is by then. None for a Config that was not loaded from a file.
    source_path: Path | None = field(default=None, repr=False, compare=False)


def _experimental_flag(section: dict, key: str) -> bool:
    """An `[experimental]` switch: only a real TOML `true` turns a feature on.

    `"false"` or `1` must not count as on (a non-empty string is truthy), so anything but a bool means off.
    """
    value = section.get(key, False)
    if isinstance(value, bool):
        return value
    logging.getLogger(__name__).warning("[experimental].%s must be true or false, got %r; keeping it off", key, value)
    return False


def _log_level(value: object) -> str:
    """`[general].log_level` in upper case; INFO when it is not one of `LOG_LEVELS`."""
    level = str(value).strip().upper()
    return level if level in LOG_LEVELS else "INFO"


def _polish_key(hotkey_data: dict) -> str:
    """`[hotkey].polish_key`, dropped when it is unknown or the same as the paste key (edited by hand)."""
    polish = normalize_hotkey_key(str(hotkey_data.get("polish_key", ""))) or ""
    paste = normalize_hotkey_key(str(hotkey_data.get("paste_key", "V"))) or ""
    return "" if polish and polish == paste else polish


def _own_voice_label(raw: object) -> str:
    """The configured own-voice label; the default when a hand-edited file uses another speaker's label."""
    label = normalize_speaker_label(raw)
    return DEFAULT_OWN_VOICE_LABEL if own_voice_label_problem(label) else label


def load_config(path: Path | None = None) -> Config:
    """Load configuration from a TOML file. Creates from example if missing.

    *path* defaults to ``config.toml`` in the working directory, or to the config folder in the installed app.
    """
    if path is None:
        path = default_config_path()
    if not path.exists():
        if EXAMPLE_CONFIG_PATH.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(EXAMPLE_CONFIG_PATH, path)
        else:
            return with_hardware_defaults(Config())

    with open(path, "rb") as f:
        data = tomllib.load(f)

    general = data.get("general", {})
    hotkey_data = data.get("hotkey", {})
    hotkey_mode = hotkey_data.get("mode", "hold")
    # A hotkey section in the file that leaves the modifiers out is an older hand-written one: keep what it meant.
    hotkey_modifiers = list(
        hotkey_data.get("modifiers", LEGACY_HOTKEY_MODIFIERS if hotkey_data else DEFAULT_HOTKEY_MODIFIERS)
    )
    hotkey_key = str(hotkey_data.get("key", ""))
    if validate_hotkey(hotkey_modifiers, hotkey_key) is not None:
        # An unusable binding would make the app unreachable; fall back to the default.
        hotkey_modifiers, hotkey_key = list(DEFAULT_HOTKEY_MODIFIERS), ""
    meeting_data = data.get("meeting", {})
    meeting_modifiers = [
        str(m).lower()
        for m in meeting_data.get("modifiers", LEGACY_MEETING_MODIFIERS if meeting_data else DEFAULT_MEETING_MODIFIERS)
    ]
    meeting_key = str(meeting_data.get("key", "M"))
    hotkey_config = HotkeyConfig(modifiers=hotkey_modifiers, key=normalize_hotkey_key(hotkey_key) or "")
    if validate_meeting_hotkey(meeting_modifiers, meeting_key, hotkey_config) is not None:
        # An unusable binding in the file falls back to the default, like the dictation hotkey.
        replacement = default_meeting_hotkey(hotkey_config)
        logger.warning(
            "Meeting hotkey %s+%s is unusable or would also start dictation; using %s+%s",
            "+".join(meeting_modifiers),
            meeting_key,
            "+".join(replacement[0]),
            replacement[1],
        )
        meeting_modifiers, meeting_key = replacement
    audio_data = data.get("audio", {})
    inference_data = data.get("inference", {})
    model = inference_data.get("model", DEFAULT_MODEL)
    server_url = inference_data.get("server_url", str(MODEL_REGISTRY[DEFAULT_MODEL]["default_url"]))
    if model not in MODEL_REGISTRY:
        # A model that was removed from the list (Voxtral, Cohere, the small Whisper sizes, Distil-Whisper):
        # use Parakeet, and its server URL too, since the saved one belongs to the old model.
        # The file is not rewritten, so say it once per process, not on every load.
        if model not in _WARNED_REMOVED_MODELS:
            _WARNED_REMOVED_MODELS.add(model)
            logger.warning("Model %r is no longer available; using %s instead.", model, DEFAULT_MODEL)
        model = DEFAULT_MODEL
        # The saved URL is kept unless it is one of the removed servers' own defaults (Voxtral's WebSocket,
        # Cohere's port), which Parakeet cannot answer; a deliberate address of another host stays.
        if server_url in _REMOVED_MODEL_URLS or server_url.startswith(("ws:", "wss:")):
            server_url = str(MODEL_REGISTRY[DEFAULT_MODEL]["default_url"])
    custom_data = inference_data.get("custom", {})
    custom_protocol = custom_data.get("protocol", "http")
    db_data = data.get("database", {})
    os_cmd_data = data.get("os_commands", {})
    experimental_data = data.get("experimental", {})
    vc_data = data.get("voice_commands", None)
    assistant_data = data.get("assistant", {})
    switcher_data = data.get("os_control", {})
    command_key = str(switcher_data.get("command_key", "Y")).upper()
    translator_data = data.get("translator", {})
    mic_guard_data = data.get("mic_guard", {})
    feedback_data = data.get("feedback", {})
    learn_data = data.get("learn_from_edits", {})
    llm_data = data.get("llm", {})
    polish_data = data.get("polish", {})
    cleanup_data = data.get("cleanup", {})
    remote_mic_data = data.get("remote_mic", {})
    support_data = data.get("support", {})

    # Backward compat: old configs use general.voice_commands_enabled (bool only)
    if vc_data is not None:
        vc_config = VoiceCommandsConfig(
            enabled=vc_data.get("enabled", True),
            symbols=list(vc_data.get("symbols", _DEFAULT_SYMBOLS)),
            numbers_as_digits=vc_data.get("numbers_as_digits", True),
            bypass_symbols=vc_data.get("bypass_symbols", False),
            structural=list(vc_data.get("structural", _DEFAULT_STRUCTURAL)),
            bypass_structural=vc_data.get("bypass_structural", False),
            press_enter=vc_data.get("press_enter", False),
        )
    else:
        vc_config = VoiceCommandsConfig(
            enabled=general.get("voice_commands_enabled", True),
            symbols=list(_DEFAULT_SYMBOLS),
            numbers_as_digits=True,
        )

    config = Config(
        injection_delay_ms=general.get("injection_delay_ms", 500),
        log_level=_log_level(general.get("log_level", "INFO")),
        log_full_text=bool(general.get("log_full_text", False)),
        hotkey=HotkeyConfig(
            modifiers=hotkey_modifiers,
            key=normalize_hotkey_key(hotkey_key) or "",
            mode=hotkey_mode if hotkey_mode in HOTKEY_MODES else "hold",
            paste_key=normalize_hotkey_key(str(hotkey_data.get("paste_key", "V"))) or "",
            polish_key=_polish_key(hotkey_data),
        ),
        meeting=MeetingConfig(
            modifiers=meeting_modifiers,
            key=normalize_hotkey_key(meeting_key) or "M",
            mic_device=str(meeting_data.get("mic_device", "")),
            output_device=str(meeting_data.get("output_device", "")),
            consent_hint=bool(meeting_data.get("consent_hint", True)),
            leave_dictation_out=bool(meeting_data.get("leave_dictation_out", True)),
            own_voice_label=_own_voice_label(meeting_data.get("own_voice_label", "")),
            live_transcript=bool(meeting_data.get("live_transcript", False)),
            store_audio=bool(meeting_data.get("store_audio", True)),
        ),
        audio=AudioConfig(
            sample_rate=audio_data.get("sample_rate", 16_000),
            chunk_size=audio_data.get("chunk_size", 4096),
        ),
        inference=InferenceConfig(
            server_url=server_url,
            transcription_delay_ms=inference_data.get("transcription_delay_ms", 480),
            model=model,
            device=inference_data.get("device", "auto"),
            compute_type=inference_data.get("compute_type", None),
            custom=CustomServerConfig(
                url=custom_data.get("url", ""),
                protocol=custom_protocol if custom_protocol in CUSTOM_PROTOCOLS else "http",
                model_name=custom_data.get("model_name", "custom-server") or "custom-server",
            ),
        ),
        database=DatabaseConfig(
            path=resolve_data_path(db_data.get("path", "nexusvox.db")),
            audio_dir=db_data.get("audio_dir", "audio"),
            store_audio=bool(db_data.get("store_audio", True)),
        ),
        os_commands=OSCommandsConfig(
            enabled=os_cmd_data.get("enabled", False),
            apps=dict(os_cmd_data.get("apps", {})),
        ),
        experimental=ExperimentalConfig(
            os_commands=_experimental_flag(experimental_data, "os_commands"),
            flag_command=_experimental_flag(experimental_data, "flag_command"),
            github_snippets=_experimental_flag(experimental_data, "github_snippets"),
            dev_tab=_experimental_flag(experimental_data, "dev_tab"),
        ),
        voice_commands=vc_config,
        assistant=AssistantConfig(
            enabled=assistant_data.get("enabled", False),
            host=assistant_data.get("host", "127.0.0.1"),
            port=int(assistant_data.get("port", 49730)),
            service=assistant_data.get("service", "voice-assistant"),
            timeout_s=float(assistant_data.get("timeout_s", 5.0)),
        ),
        os_control=OsControlConfig(
            enabled=switcher_data.get("enabled", False),
            command_key=command_key if command_key in COMMAND_KEY_VKS else "Y",
            host=switcher_data.get("host", "127.0.0.1"),
            port=int(switcher_data.get("port", 49730)),
            service=switcher_data.get("service", "os-control"),
            timeout_s=float(switcher_data.get("timeout_s", 5.0)),
        ),
        translator=TranslatorConfig(
            enabled=translator_data.get("enabled", False),
            url=translator_data.get("url", "http://127.0.0.1:8003/translate"),
            timeout_s=float(translator_data.get("timeout_s", 8.0)),
        ),
        mic_guard=MicGuardConfig(
            enabled=mic_guard_data.get("enabled", False),
            watchdog_s=float(mic_guard_data.get("watchdog_s", 30.0)),
        ),
        feedback=FeedbackConfig(
            mode=feedback_data.get("mode") if feedback_data.get("mode") in FEEDBACK_MODES else "sound",
        ),
        llm=LlmConfig(
            url=str(llm_data.get("url", LlmConfig.url)),
            protocol=llm_data.get("protocol") if llm_data.get("protocol") in LLM_PROTOCOLS else "ollama",
            model=_llm_model(llm_data),
            api_key=str(llm_data.get("api_key", "")),
            timeout_s=float(llm_data.get("timeout_s", LlmConfig.timeout_s)),
            keep_alive=str(llm_data.get("keep_alive", LlmConfig.keep_alive)),
        ),
        polish=PolishConfig(in_terminals=bool(polish_data.get("in_terminals", False))),
        cleanup=_cleanup_config(cleanup_data),
        learn_from_edits=LearnFromEditsConfig(
            enabled=learn_data.get("enabled", False),
            window_s=float(learn_data.get("window_s", 20.0)),
        ),
        remote_mic=RemoteMicConfig(
            enabled=remote_mic_data.get("enabled", False),
            host=remote_mic_data.get("host", "127.0.0.1"),
            port=int(remote_mic_data.get("port", 47393)),
            token=str(remote_mic_data.get("token", "")),
        ),
        support=SupportConfig(
            feedback_form_url=_feedback_form_url(support_data.get("feedback_form_url", "")),
        ),
    )
    config.source_path = path.resolve()
    return config


_config_lock = threading.Lock()


def save_config(config: Config, path: Path | None = None) -> None:
    """Write the current configuration back to TOML: to *path*, else to the file it was loaded from."""
    path = path or config.source_path or default_config_path()
    with _config_lock:
        symbols_toml = "[{}]".format(", ".join(f'"{s}"' for s in config.voice_commands.symbols))
        structural_toml = "[{}]".format(", ".join(f'"{s}"' for s in config.voice_commands.structural))
        lines = [
            "[general]",
            f"injection_delay_ms = {config.injection_delay_ms}",
            f'log_level = "{config.log_level}"',
            f"log_full_text = {'true' if config.log_full_text else 'false'}",
            "",
            "[hotkey]",
            "modifiers = [{}]".format(", ".join(f'"{m}"' for m in config.hotkey.modifiers)),
            f'key = "{config.hotkey.key}"',
            f'mode = "{config.hotkey.mode}"',
            f'paste_key = "{config.hotkey.paste_key}"',
            f'polish_key = "{config.hotkey.polish_key}"',
            "",
            "[meeting]",
            "modifiers = [{}]".format(", ".join(f'"{m}"' for m in config.meeting.modifiers)),
            f'key = "{config.meeting.key}"',
            f"mic_device = {json.dumps(config.meeting.mic_device)}",
            f"output_device = {json.dumps(config.meeting.output_device)}",
            f"consent_hint = {'true' if config.meeting.consent_hint else 'false'}",
            f"leave_dictation_out = {'true' if config.meeting.leave_dictation_out else 'false'}",
            f"own_voice_label = {json.dumps(config.meeting.own_voice_label)}",
            f"live_transcript = {'true' if config.meeting.live_transcript else 'false'}",
            f"store_audio = {'true' if config.meeting.store_audio else 'false'}",
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
            "[inference.custom]",
            f'url = "{config.inference.custom.url}"',
            f'protocol = "{config.inference.custom.protocol}"',
            f'model_name = "{config.inference.custom.model_name}"',
            "",
            "[database]",
            f'path = "{config.database.path}"',
            f'audio_dir = "{config.database.audio_dir}"',
            f"store_audio = {'true' if config.database.store_audio else 'false'}",
            "",
            "[os_commands]",
            f"enabled = {'true' if config.os_commands.enabled else 'false'}",
            "",
            "[os_commands.apps]",
            *[f'{name} = "{exe}"' for name, exe in config.os_commands.apps.items()],
            "",
            "[experimental]",
            f"os_commands = {'true' if config.experimental.os_commands else 'false'}",
            f"flag_command = {'true' if config.experimental.flag_command else 'false'}",
            f"github_snippets = {'true' if config.experimental.github_snippets else 'false'}",
            f"dev_tab = {'true' if config.experimental.dev_tab else 'false'}",
            "",
            "[voice_commands]",
            f"enabled = {'true' if config.voice_commands.enabled else 'false'}",
            f"numbers_as_digits = {'true' if config.voice_commands.numbers_as_digits else 'false'}",
            f"bypass_symbols = {'true' if config.voice_commands.bypass_symbols else 'false'}",
            f"symbols = {symbols_toml}",
            f"bypass_structural = {'true' if config.voice_commands.bypass_structural else 'false'}",
            f"structural = {structural_toml}",
            f"press_enter = {'true' if config.voice_commands.press_enter else 'false'}",
            "",
            "[assistant]",
            f"enabled = {'true' if config.assistant.enabled else 'false'}",
            f'host = "{config.assistant.host}"',
            f"port = {config.assistant.port}",
            f'service = "{config.assistant.service}"',
            f"timeout_s = {config.assistant.timeout_s}",
            "",
            "[os_control]",
            f"enabled = {'true' if config.os_control.enabled else 'false'}",
            f'command_key = "{config.os_control.command_key}"',
            f'host = "{config.os_control.host}"',
            f"port = {config.os_control.port}",
            f'service = "{config.os_control.service}"',
            f"timeout_s = {config.os_control.timeout_s}",
            "",
            "[translator]",
            f"enabled = {'true' if config.translator.enabled else 'false'}",
            f'url = "{config.translator.url}"',
            f"timeout_s = {config.translator.timeout_s}",
            "",
            "[mic_guard]",
            f"enabled = {'true' if config.mic_guard.enabled else 'false'}",
            f"watchdog_s = {config.mic_guard.watchdog_s}",
            "",
            "[feedback]",
            f'mode = "{config.feedback.mode}"',
            "",
            "[llm]",
            f"url = {json.dumps(config.llm.url)}",
            f'protocol = "{config.llm.protocol}"',
            # A model that is just the hardware default stays unset, so it keeps following the hardware.
            *(
                [f"model = {json.dumps(config.llm.model)}"]
                if config.llm.model != auto_llm_model()
                else ["# model = (unset: gemma4:e4b with a CUDA GPU of 12 GB or more, else gemma4:e2b)"]
            ),
            # A free-form secret: escape it, a `"` or `\` would break the file.
            f"api_key = {json.dumps(config.llm.api_key)}",
            f"timeout_s = {config.llm.timeout_s}",
            f"keep_alive = {json.dumps(config.llm.keep_alive)}",
            "",
            "[polish]",
            f"in_terminals = {'true' if config.polish.in_terminals else 'false'}",
            "",
            "[cleanup]",
            f'level = "{config.cleanup.level}"',
            *(
                [f"timeout_s = {config.cleanup.timeout_s}"]
                if config.cleanup.timeout_s != auto_cleanup_timeout()
                else ["# timeout_s = (unset: 3 s with a CUDA GPU, 10 s without)"]
            ),
            "",
            "[learn_from_edits]",
            f"enabled = {'true' if config.learn_from_edits.enabled else 'false'}",
            f"window_s = {config.learn_from_edits.window_s}",
            "",
            "[remote_mic]",
            f"enabled = {'true' if config.remote_mic.enabled else 'false'}",
            f'host = "{config.remote_mic.host}"',
            f"port = {config.remote_mic.port}",
            # A free-form secret: escape it, a `"` or `\` would break the file.
            f"token = {json.dumps(config.remote_mic.token)}",
            "",
            "[support]",
            f"feedback_form_url = {json.dumps(config.support.feedback_form_url)}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
