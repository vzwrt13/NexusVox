"""Configuration loading from TOML file.

The sections of the file are defined in `config_sections`; every name stays importable from here, the one public
path. What reads the hardware or the template stays in this module, so a patch of `nexusvox.config.cuda_available`
or `EXAMPLE_CONFIG_PATH` reaches the code that uses it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import tomllib
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
from .system.paths import default_config_path, resolve_data_path

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
    # `[database].path` as the file wrote it, before `load_config` resolved it into the data folder of the installed
    # app. `save_config` writes it back while it still resolves to `database.path`, so a relative path stays relative.
    database_path_configured: str | None = field(default=None, repr=False, compare=False)


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

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        # A file the old `save_config` broke (an unescaped Windows path) would otherwise keep the app from starting.
        # Keep it next to the config so nothing is lost, and start from the template.
        backup = path.with_name(f"{path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.replace(path, backup)
        except OSError:
            raise exc from None
        logger.warning("%s is not valid TOML (%s); moved it to %s and started from the defaults", path, exc, backup)
        return load_config(path)

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
    config.database_path_configured = db_data.get("path", "nexusvox.db")
    return config


_config_lock = threading.Lock()


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
# A lone surrogate (JSON such as "\ud83d" produces one) cannot be encoded as UTF-8.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _toml_str(value: str) -> str:
    """Render *value* as a TOML basic string.

    ``json.dumps`` without ``ensure_ascii`` escapes the quote, the backslash and every control character with the
    same escapes TOML knows; DEL is the one control character it leaves and TOML forbids. Non-ASCII stays literal
    (the file is UTF-8), and a lone surrogate, which cannot be written, becomes U+FFFD.
    """
    if not isinstance(value, str):
        raise TypeError(f"a config string must be str, not {type(value).__name__}")
    return json.dumps(_LONE_SURROGATE.sub("�", value), ensure_ascii=False).replace("\x7f", "\\u007f")


def _toml_key(name: str) -> str:
    """A TOML key: bare when it only holds letters, digits, ``_`` and ``-``, else a quoted one."""
    return name if name and _BARE_KEY.fullmatch(name) else _toml_str(name)


def _toml_value(value: object) -> str:
    """Render a bool, number, string or list of those; anything else is a bug in the caller and raises."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return "inf" if value == math.inf else "-inf" if value == -math.inf else repr(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, (list, tuple)):
        return "[{}]".format(", ".join(_toml_value(item) for item in value))
    raise TypeError(f"cannot write a {type(value).__name__} to the config")


def _section(name: str, *items: tuple[str, object] | str) -> list[str]:
    """The lines of one ``[name]`` table: ``(key, value)`` pairs, and plain strings which are written as they are
    (comments). Every value goes through `_toml_value`, so no field can be written unescaped."""
    lines = [f"[{name}]"]
    for item in items:
        if isinstance(item, str):
            lines.append(item)
        else:
            key, value = item
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    lines.append("")
    return lines


def _only_strings(items: object, what: str) -> list[str]:
    """The string entries of a list a setter filled from JSON; anything else is dropped with a warning."""
    kept = [item for item in items if isinstance(item, str)]
    if len(kept) != len(items):
        logger.warning("Dropped %d non-string entries of %s from the saved config", len(items) - len(kept), what)
    return kept


def _only_string_pairs(mapping: dict, what: str) -> dict[str, str]:
    """The string -> string entries of a table a setter filled from JSON; anything else is dropped with a warning."""
    kept = {k: v for k, v in mapping.items() if isinstance(k, str) and isinstance(v, str)}
    if len(kept) != len(mapping):
        logger.warning(
            "Dropped %d entries of %s that are not text from the saved config", len(mapping) - len(kept), what
        )
    return kept


def _configured_database_path(config: Config) -> str:
    """What `[database].path` should say: the value the file had while it still resolves to the live path.

    `load_config` resolves a relative path into the data folder of the installed app; writing that back would pin
    the profile of the user who saved first.
    """
    configured = config.database_path_configured
    if configured is not None and resolve_data_path(configured) == config.database.path:
        return configured
    return config.database.path


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace *path* with *data* in one step: a failure or a kill mid-write leaves the old file untouched."""
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def save_config(config: Config, path: Path | None = None) -> None:
    """Write the current configuration back to TOML: to *path*, else to the file it was loaded from.

    The whole text is built and encoded before the file is touched, and then swapped in atomically, so a value that
    cannot be written (a TypeError) or a failed write leaves the existing file as it was.
    """
    path = path or config.source_path or default_config_path()
    with _config_lock:
        lines = [
            *_section(
                "general",
                ("injection_delay_ms", config.injection_delay_ms),
                ("log_level", config.log_level),
                ("log_full_text", config.log_full_text),
            ),
            *_section(
                "hotkey",
                ("modifiers", config.hotkey.modifiers),
                ("key", config.hotkey.key),
                ("mode", config.hotkey.mode),
                ("paste_key", config.hotkey.paste_key),
                ("polish_key", config.hotkey.polish_key),
                ("max_recording_s", config.hotkey.max_recording_s),
            ),
            *_section(
                "meeting",
                ("modifiers", config.meeting.modifiers),
                ("key", config.meeting.key),
                ("mic_device", config.meeting.mic_device),
                ("output_device", config.meeting.output_device),
                ("consent_hint", config.meeting.consent_hint),
                ("leave_dictation_out", config.meeting.leave_dictation_out),
                ("own_voice_label", config.meeting.own_voice_label),
                ("live_transcript", config.meeting.live_transcript),
                ("store_audio", config.meeting.store_audio),
            ),
            *_section(
                "audio",
                ("sample_rate", config.audio.sample_rate),
                ("chunk_size", config.audio.chunk_size),
            ),
            *_section(
                "inference",
                ("server_url", config.inference.server_url),
                ("transcription_delay_ms", config.inference.transcription_delay_ms),
                ("model", config.inference.model),
                ("device", config.inference.device),
                *([("compute_type", config.inference.compute_type)] if config.inference.compute_type else []),
            ),
            *_section(
                "inference.custom",
                ("url", config.inference.custom.url),
                ("protocol", config.inference.custom.protocol),
                ("model_name", config.inference.custom.model_name),
            ),
            *_section(
                "database",
                ("path", _configured_database_path(config)),
                ("audio_dir", config.database.audio_dir),
                ("store_audio", config.database.store_audio),
            ),
            *_section("os_commands", ("enabled", config.os_commands.enabled)),
            *_section(
                "os_commands.apps",
                *_only_string_pairs(config.os_commands.apps, "[os_commands.apps]").items(),
            ),
            *_section(
                "experimental",
                ("os_commands", config.experimental.os_commands),
                ("flag_command", config.experimental.flag_command),
                ("dev_tab", config.experimental.dev_tab),
            ),
            *_section(
                "voice_commands",
                ("enabled", config.voice_commands.enabled),
                ("numbers_as_digits", config.voice_commands.numbers_as_digits),
                ("bypass_symbols", config.voice_commands.bypass_symbols),
                ("symbols", _only_strings(config.voice_commands.symbols, "[voice_commands].symbols")),
                ("bypass_structural", config.voice_commands.bypass_structural),
                ("structural", _only_strings(config.voice_commands.structural, "[voice_commands].structural")),
                ("press_enter", config.voice_commands.press_enter),
            ),
            *_section(
                "assistant",
                ("enabled", config.assistant.enabled),
                ("host", config.assistant.host),
                ("port", config.assistant.port),
                ("service", config.assistant.service),
                ("timeout_s", config.assistant.timeout_s),
            ),
            *_section(
                "os_control",
                ("enabled", config.os_control.enabled),
                ("command_key", config.os_control.command_key),
                ("host", config.os_control.host),
                ("port", config.os_control.port),
                ("service", config.os_control.service),
                ("timeout_s", config.os_control.timeout_s),
            ),
            *_section(
                "translator",
                ("enabled", config.translator.enabled),
                ("url", config.translator.url),
                ("timeout_s", config.translator.timeout_s),
            ),
            *_section(
                "mic_guard",
                ("enabled", config.mic_guard.enabled),
                ("watchdog_s", config.mic_guard.watchdog_s),
            ),
            *_section("feedback", ("mode", config.feedback.mode)),
            *_section(
                "llm",
                ("url", config.llm.url),
                ("protocol", config.llm.protocol),
                # A model that is just the hardware default stays unset, so it keeps following the hardware.
                *(
                    [("model", config.llm.model)]
                    if config.llm.model != auto_llm_model()
                    else ["# model = (unset: gemma4:e4b with a CUDA GPU of 12 GB or more, else gemma4:e2b)"]
                ),
                ("api_key", config.llm.api_key),
                ("timeout_s", config.llm.timeout_s),
                ("keep_alive", config.llm.keep_alive),
            ),
            *_section("polish", ("in_terminals", config.polish.in_terminals)),
            *_section(
                "cleanup",
                ("level", config.cleanup.level),
                *(
                    [("timeout_s", config.cleanup.timeout_s)]
                    if config.cleanup.timeout_s != auto_cleanup_timeout()
                    else ["# timeout_s = (unset: 3 s with a CUDA GPU, 10 s without)"]
                ),
            ),
            *_section(
                "learn_from_edits",
                ("enabled", config.learn_from_edits.enabled),
                ("window_s", config.learn_from_edits.window_s),
            ),
            *_section(
                "remote_mic",
                ("enabled", config.remote_mic.enabled),
                ("host", config.remote_mic.host),
                ("port", config.remote_mic.port),
                ("token", config.remote_mic.token),
            ),
            *_section("support", ("feedback_form_url", config.support.feedback_form_url)),
        ]
        # The platform's line ending, as `Path.write_text` wrote it before.
        _write_atomic(path, os.linesep.join(lines).encode("utf-8"))
