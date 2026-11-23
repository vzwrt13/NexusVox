"""The Settings > Log File *Copy diagnostics* text: what a maintainer needs to help with a problem, in one block.

Built on the click and returned to the page, which puts it on the clipboard. Nothing here sends anything: there is
no network call. The block holds the app version, the Windows build, package versions, CPU and GPU, the model status,
the config with every secret removed and the end of today's log with dictated text cut out (`report_log`). No
transcript, dictation or audio content is read: the database is never opened and the config holds none.
"""

from __future__ import annotations

import dataclasses
import logging
import platform
import re
import subprocess
from importlib import metadata

from nexusvox import __version__
from nexusvox.config import Config
from nexusvox.system.feedback_form import windows_version
from nexusvox.system.feedback_send import report_log, strip_dictated_text

logger = logging.getLogger(__name__)

DIAGNOSTICS_LOG_LINES = 200
REDACTED = "<redacted>"
PACKAGES = (
    "faster-whisper",
    "onnx-asr",
    "onnxruntime",
    "av",
    "numpy",
    "sounddevice",
    "pynput",
    "flask",
    "httpx",
    "sqlalchemy",
    "websockets",
    "pystray",
    "Pillow",
    "lingua-language-detector",
)
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|credential|auth)")
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_NOT_CONFIG = {"source_path", "database_path_configured"}  # file paths of this PC, not settings


def _redact(value: object, key: str = "") -> object:
    """`value` with every secret replaced: a value under a secret-looking key, and credentials inside a URL."""
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items() if k not in _NOT_CONFIG}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        if _SECRET_KEY.search(key) and value:
            return REDACTED
        return _URL_CREDENTIALS.sub(r"\1" + REDACTED + "@", value)
    return value


def redacted_config(config: Config) -> dict:
    """The config as plain data: secrets removed, no file paths of this PC."""
    return _redact(dataclasses.asdict(config))  # type: ignore[return-value]


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    return str(value)


def _config_lines(data: dict, prefix: str = "") -> list[str]:
    plain = [f"{key} = {_format_value(v)}" for key, v in data.items() if not isinstance(v, dict)]
    lines = ([f"[{prefix}]"] if prefix and plain else []) + plain
    for key, value in data.items():
        if isinstance(value, dict):
            lines += _config_lines(value, f"{prefix}.{key}" if prefix else key)
    return lines


def package_versions() -> dict[str, str]:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def cpu_name() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except (ImportError, OSError):
        return platform.processor() or "unknown"


def gpu_name() -> str:
    """The first NVIDIA GPU with its driver and VRAM, or "none" (nvidia-smi is asked once, with a short timeout)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        return out.splitlines()[0].strip() or "none"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "none (no NVIDIA GPU found)"


def build_diagnostics(config: Config, *, model_status: dict | None = None, device: dict | None = None) -> str:
    """The whole block as text. `model_status` and `device` are the dashboard's own answers for the model and device."""
    model_status = model_status or {}
    device = device or {}
    lines = [
        "NexusVox diagnostics",
        "",
        f"App version: {__version__}",
        f"Windows: {windows_version()}",
        f"Python: {platform.python_version()}",
        f"CPU: {cpu_name()}",
        f"GPU: {gpu_name()}",
        "",
        "Model:",
        f"  selected: {config.inference.model}",
        f"  device: requested {device.get('requested', '?')}, running on {device.get('active') or 'unknown'}",
        f"  status: {model_status.get('status', 'unknown')}",
        "",
        "Packages:",
        *(f"  {name} {version}" for name, version in package_versions().items()),
        "",
        "Config (secrets removed):",
        *_config_lines(redacted_config(config)),
        "",
        f"Log (last {DIAGNOSTICS_LOG_LINES} lines, dictated text removed):",
        report_log(lines=DIAGNOSTICS_LOG_LINES) or "(no log)",
    ]
    return strip_dictated_text("\n".join(lines))
