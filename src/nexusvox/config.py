"""Configuration loading from TOML file.

The sections of the file are defined in `config_sections`; every name stays importable from here, the one public
path. What reads the hardware or the template stays in this module, so a patch of `nexusvox.config.cuda_available`
or `EXAMPLE_CONFIG_PATH` reaches the code that uses it.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import threading
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .config_sections.features import (  # noqa: F401
    FEEDBACK_MODES,
    ExperimentalConfig,
    FeedbackConfig,
    LearnFromEditsConfig,
    MicGuardConfig,
    OSCommandsConfig,
)
from .config_sections.hotkey import (  # noqa: F401
    DEFAULT_HOTKEY_MODIFIERS,
    DEFAULT_MEETING_KEY,
    DEFAULT_MEETING_MODIFIERS,
    HOTKEY_KEY_VKS,
    HOTKEY_MODES,
    HOTKEY_MODIFIERS,
    LEGACY_HOTKEY_MODIFIERS,
    LEGACY_MEETING_KEY,
    LEGACY_MEETING_MODIFIERS,
    HotkeyConfig,
    normalize_hotkey_key,
    validate_hotkey,
)
from .config_sections.inference import (  # noqa: F401
    CUSTOM_PROTOCOLS,
    DEFAULT_MODEL,
    MODEL_REGISTRY,
    CustomServerConfig,
    InferenceConfig,
    model_download_state,
    model_needs_docker,
)
from .config_sections.integrations import (  # noqa: F401
    COMMAND_KEY_VKS,
    AssistantConfig,
    OsControlConfig,
    RemoteMicConfig,
    SupportConfig,
    TranslatorConfig,
)
from .config_sections.llm import (  # noqa: F401
    CLEANUP_LEVELS,
    CLEANUP_TIMEOUT_CPU_S,
    LLM_MODEL_CPU,
    LLM_PROTOCOLS,
    CleanupConfig,
    LlmConfig,
    PolishConfig,
)
from .config_sections.meeting import (  # noqa: F401
    _MEETING_HOTKEY_FALLBACKS,
    DEFAULT_OWN_VOICE_LABEL,
    MAX_SPEAKER_LABEL_CHARS,
    MeetingConfig,
    default_meeting_hotkey,
    meeting_hotkey_clash,
    normalize_speaker_label,
    numbered_speaker_label,
    own_voice_label_problem,
    validate_meeting_hotkey,
)
from .config_sections.storage import AudioConfig, DatabaseConfig  # noqa: F401
from .config_sections.voice_commands import (  # noqa: F401
    _DEFAULT_STRUCTURAL,
    _DEFAULT_SYMBOLS,
    VoiceCommandsConfig,
)
from .paths import default_config_path, resolve_data_path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config.toml")
# Resolved next to this module, not relative to the repository root. The template
# ships inside the package (see [tool.setuptools.package-data]), so an installed
# wheel finds it in site-packages exactly as a source checkout finds it in src/.
# A path walking up to the repository root works only in a checkout and silently
# leaves wheel users without a config.toml at all.
EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parent / "config.example.toml"

# Default URLs of the removed Voxtral and Cohere containers.
_REMOVED_MODEL_URLS = {"ws://localhost:8000/v1/realtime", "http://localhost:8001/v1/audio/transcriptions"}
_WARNED_REMOVED_MODELS: set[str] = set()


# `[general].log_level`: names of the standard logging levels a user may pick.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


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


def _feedback_form_url(value: object) -> str:
    """The configured form URL; a value that is not an http(s) URL is logged and treated as unset."""
    url = value.strip() if isinstance(value, str) else ""
    if value and not url.lower().startswith(("http://", "https://")):
        logging.getLogger(__name__).warning(
            "[support].feedback_form_url must be an http(s) URL; the Send feedback button stays hidden"
        )
        return ""
    return url


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


def _max_recording_s(value: object) -> int:
    """`[hotkey].max_recording_s`: whole seconds, 0 turns the cap off; anything unusable keeps the 10-minute default."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 600
    if value == 0:
        return 0
    if not math.isfinite(value) or value < 1:
        return 600
    return int(value)


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
    meeting_key = str(meeting_data.get("key", LEGACY_MEETING_KEY if meeting_data else DEFAULT_MEETING_KEY))
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
            max_recording_s=_max_recording_s(hotkey_data.get("max_recording_s", 600)),
        ),
        meeting=MeetingConfig(
            modifiers=meeting_modifiers,
            key=normalize_hotkey_key(meeting_key) or DEFAULT_MEETING_KEY,
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


def _toml_string(value: object) -> str:
    """`value` as a TOML basic string. JSON's escapes are valid TOML, except that `ensure_ascii` writes an emoji as a
    surrogate pair, which TOML refuses, and DEL is the one control character JSON leaves as it is."""
    return json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_list(values: Iterable[object]) -> str:
    return "[{}]".format(", ".join(_toml_string(value) for value in values))


def _toml_key(name: str) -> str:
    """A bare key where TOML allows one (`notepad`), a quoted one otherwise (`"my app"`)."""
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_string(name)


def save_config(config: Config, path: Path | None = None) -> None:
    """Write the current configuration back to TOML: to *path*, else to the file it was loaded from."""
    path = path or config.source_path or default_config_path()
    with _config_lock:
        lines = [
            "[general]",
            f"injection_delay_ms = {config.injection_delay_ms}",
            f"log_level = {_toml_string(config.log_level)}",
            f"log_full_text = {'true' if config.log_full_text else 'false'}",
            "",
            "[hotkey]",
            f"modifiers = {_toml_list(config.hotkey.modifiers)}",
            f"key = {_toml_string(config.hotkey.key)}",
            f"mode = {_toml_string(config.hotkey.mode)}",
            f"paste_key = {_toml_string(config.hotkey.paste_key)}",
            f"polish_key = {_toml_string(config.hotkey.polish_key)}",
            f"max_recording_s = {config.hotkey.max_recording_s}",
            "",
            "[meeting]",
            f"modifiers = {_toml_list(config.meeting.modifiers)}",
            f"key = {_toml_string(config.meeting.key)}",
            f"mic_device = {_toml_string(config.meeting.mic_device)}",
            f"output_device = {_toml_string(config.meeting.output_device)}",
            f"consent_hint = {'true' if config.meeting.consent_hint else 'false'}",
            f"leave_dictation_out = {'true' if config.meeting.leave_dictation_out else 'false'}",
            f"own_voice_label = {_toml_string(config.meeting.own_voice_label)}",
            f"live_transcript = {'true' if config.meeting.live_transcript else 'false'}",
            f"store_audio = {'true' if config.meeting.store_audio else 'false'}",
            "",
            "[audio]",
            f"sample_rate = {config.audio.sample_rate}",
            f"chunk_size = {config.audio.chunk_size}",
            "",
            "[inference]",
            f"server_url = {_toml_string(config.inference.server_url)}",
            f"transcription_delay_ms = {config.inference.transcription_delay_ms}",
            f"model = {_toml_string(config.inference.model)}",
            f"device = {_toml_string(config.inference.device)}",
            *(
                [f"compute_type = {_toml_string(config.inference.compute_type)}"]
                if config.inference.compute_type
                else []
            ),
            "",
            "[inference.custom]",
            f"url = {_toml_string(config.inference.custom.url)}",
            f"protocol = {_toml_string(config.inference.custom.protocol)}",
            f"model_name = {_toml_string(config.inference.custom.model_name)}",
            "",
            "[database]",
            f"path = {_toml_string(config.database.path)}",
            f"audio_dir = {_toml_string(config.database.audio_dir)}",
            f"store_audio = {'true' if config.database.store_audio else 'false'}",
            "",
            "[os_commands]",
            f"enabled = {'true' if config.os_commands.enabled else 'false'}",
            "",
            "[os_commands.apps]",
            *[f"{_toml_key(name)} = {_toml_string(exe)}" for name, exe in config.os_commands.apps.items()],
            "",
            "[experimental]",
            f"os_commands = {'true' if config.experimental.os_commands else 'false'}",
            f"flag_command = {'true' if config.experimental.flag_command else 'false'}",
            f"dev_tab = {'true' if config.experimental.dev_tab else 'false'}",
            "",
            "[voice_commands]",
            f"enabled = {'true' if config.voice_commands.enabled else 'false'}",
            f"numbers_as_digits = {'true' if config.voice_commands.numbers_as_digits else 'false'}",
            f"bypass_symbols = {'true' if config.voice_commands.bypass_symbols else 'false'}",
            f"symbols = {_toml_list(config.voice_commands.symbols)}",
            f"bypass_structural = {'true' if config.voice_commands.bypass_structural else 'false'}",
            f"structural = {_toml_list(config.voice_commands.structural)}",
            f"press_enter = {'true' if config.voice_commands.press_enter else 'false'}",
            "",
            "[assistant]",
            f"enabled = {'true' if config.assistant.enabled else 'false'}",
            f"host = {_toml_string(config.assistant.host)}",
            f"port = {config.assistant.port}",
            f"service = {_toml_string(config.assistant.service)}",
            f"timeout_s = {config.assistant.timeout_s}",
            "",
            "[os_control]",
            f"enabled = {'true' if config.os_control.enabled else 'false'}",
            f"command_key = {_toml_string(config.os_control.command_key)}",
            f"host = {_toml_string(config.os_control.host)}",
            f"port = {config.os_control.port}",
            f"service = {_toml_string(config.os_control.service)}",
            f"timeout_s = {config.os_control.timeout_s}",
            "",
            "[translator]",
            f"enabled = {'true' if config.translator.enabled else 'false'}",
            f"url = {_toml_string(config.translator.url)}",
            f"timeout_s = {config.translator.timeout_s}",
            "",
            "[mic_guard]",
            f"enabled = {'true' if config.mic_guard.enabled else 'false'}",
            f"watchdog_s = {config.mic_guard.watchdog_s}",
            "",
            "[feedback]",
            f"mode = {_toml_string(config.feedback.mode)}",
            "",
            "[llm]",
            f"url = {_toml_string(config.llm.url)}",
            f"protocol = {_toml_string(config.llm.protocol)}",
            # A model that is just the hardware default stays unset, so it keeps following the hardware.
            *(
                [f"model = {_toml_string(config.llm.model)}"]
                if config.llm.model != auto_llm_model()
                else ["# model = (unset: gemma4:e4b with a CUDA GPU of 12 GB or more, else gemma4:e2b)"]
            ),
            f"api_key = {_toml_string(config.llm.api_key)}",
            f"timeout_s = {config.llm.timeout_s}",
            f"keep_alive = {_toml_string(config.llm.keep_alive)}",
            "",
            "[polish]",
            f"in_terminals = {'true' if config.polish.in_terminals else 'false'}",
            "",
            "[cleanup]",
            f"level = {_toml_string(config.cleanup.level)}",
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
            f"host = {_toml_string(config.remote_mic.host)}",
            f"port = {config.remote_mic.port}",
            f"token = {_toml_string(config.remote_mic.token)}",
            "",
            "[support]",
            f"feedback_form_url = {_toml_string(config.support.feedback_form_url)}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
