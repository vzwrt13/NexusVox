r"""Where NexusVox keeps its config, data and models.

From source the answer is the working directory, as it always was. In a frozen build (the installed
app) there is no meaningful working directory, so everything lives in fixed per-user folders:

- config: ``%APPDATA%\NexusVox``
- database and audio: the data folder, ``%LOCALAPPDATA%\NexusVox\data``
- speech models: ``%LOCALAPPDATA%\NexusVox\models``

The models folder is defined only here; a later setting that lets the user pick another folder
changes ``models_dir`` and nothing else.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "NexusVox"

DOCKER_UNAVAILABLE_MSG = (
    "Docker-backed models are not available in the installed app. Use a CPU model (Parakeet or Whisper), "
    "or see the Advanced docs for the Docker setup."
)


def is_frozen() -> bool:
    """True in an installed (PyInstaller) build."""
    return bool(getattr(sys, "frozen", False))


def _env_dir(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else fallback


def config_dir() -> Path:
    r"""The folder holding config.toml: ``%APPDATA%\NexusVox`` when frozen, the working directory otherwise."""
    if not is_frozen():
        return Path.cwd()
    return _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / APP_DIR_NAME


def _local_dir() -> Path:
    return _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / APP_DIR_NAME


def data_dir() -> Path:
    r"""The folder holding the database and audio: ``%LOCALAPPDATA%\NexusVox\data`` when frozen, else the cwd."""
    return _local_dir() / "data" if is_frozen() else Path.cwd()


def models_dir() -> Path | None:
    """Where speech models are downloaded when frozen; None from source (the Hugging Face cache, as before)."""
    return _local_dir() / "models" if is_frozen() else None


def default_config_path() -> Path:
    """The config.toml to load: relative ``config.toml`` from source, the config folder when frozen."""
    return Path("config.toml") if not is_frozen() else config_dir() / "config.toml"


def resolve_data_path(value: str) -> str:
    """A relative database path means the data folder when frozen; source runs and absolute paths are unchanged."""
    if not is_frozen() or Path(value).is_absolute():
        return value
    return str(data_dir() / value)


def ensure_dirs() -> None:
    """Create the per-user folders on first start. Does nothing from source."""
    if not is_frozen():
        return
    for folder in (config_dir(), data_dir(), models_dir()):
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)


def apply_models_dir() -> None:
    """Point the Hugging Face cache at the models folder (frozen only); call before any model library loads."""
    folder = models_dir()
    if folder is not None:
        os.environ["HF_HUB_CACHE"] = str(folder)
