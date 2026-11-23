"""Consent for speech model downloads: no model is downloaded until the user has started it.

The in-process engines (Parakeet and Whisper on the CPU) load their model from the Hugging Face cache and would
download it there on first use. They ask `require` first: a copy that is known to be missing, and whose download the
user has not started in this session, raises `ModelNotDownloaded` instead of downloading. The dashboard (first-run
setup, Settings > Models) shows the model and its size and starts the download with `start`, which allows it and
runs the engine's load in a thread; `job` and `progress` report on it. A failed download takes the permission back,
so only the next click (Retry) downloads again.

A copy that cannot be checked (the cache is unreadable) counts as present: the engine then loads as before.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from ..config import MODEL_REGISTRY, model_download_state
from ..system.paths import models_dir

logger = logging.getLogger(__name__)

MISSING_MESSAGE = (
    "The speech model is not downloaded yet. Download it in the dashboard: in the first-run setup or under "
    "Settings > Models."
)
OFFLINE_MESSAGE = (
    "Hugging Face, where the speech model is downloaded from, cannot be reached. Check that this PC is online, "
    "then click Retry."
)

# Exception class names that mean "no connection": requests, httpx, urllib3 and huggingface_hub's own (which it
# raises when the files are neither reachable nor cached). Matched by name, so none of those libraries is imported.
_OFFLINE_NAMES = frozenset(
    {
        "ConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectTimeoutError",
        "NewConnectionError",
        "MaxRetryError",
        "NameResolutionError",
        "LocalEntryNotFoundError",
        "OfflineModeIsEnabled",
    }
)


class ModelNotDownloaded(RuntimeError):
    """The model is not on this PC and the user has not started its download."""

    def __init__(self, model_id: str) -> None:
        super().__init__(MISSING_MESSAGE)
        self.model_id = model_id


_lock = threading.Lock()
_allowed: set[str] = set()
# model id -> {"state": "downloading" | "ready" | "error", "error": str | None, "offline": bool}
_jobs: dict[str, dict] = {}


def is_allowed(model_id: str) -> bool:
    """Whether the user started the download of `model_id` in this session (and it has not failed since)."""
    with _lock:
        return model_id in _allowed


def is_downloaded(model_id: str) -> bool | None:
    """Whether the in-process copy is on disk; None when that cannot be told (or the model has no such copy)."""
    return model_download_state(model_id, models_dir())["downloaded"]  # type: ignore[return-value]


def missing(model_id: str | None) -> bool:
    """True when the copy of `model_id` is known to be missing and the user has not started its download."""
    if not model_id or is_allowed(model_id):
        return False
    return is_downloaded(model_id) is False


def require(model_id: str | None) -> None:
    """Raise `ModelNotDownloaded` instead of letting an engine download a model the user has not asked for."""
    if missing(model_id):
        raise ModelNotDownloaded(str(model_id))


def job(model_id: str) -> dict:
    """The last download the user started for `model_id`: `state` is idle, downloading, ready or error."""
    with _lock:
        return dict(_jobs.get(model_id) or {"state": "idle", "error": None, "offline": False})


def start(model_id: str, load: Callable[[], object]) -> bool:
    """Allow the download of `model_id` and run `load` (the engine's model load) in a thread.

    False when a download of it is already running. `load` raises on failure; the reason is kept for the dashboard,
    with `offline` set when there was no connection.
    """
    with _lock:
        if (_jobs.get(model_id) or {}).get("state") == "downloading":
            return False
        _allowed.add(model_id)
        _jobs[model_id] = {"state": "downloading", "error": None, "offline": False}
    logger.info("Download of the speech model %s started by the user", model_id)
    threading.Thread(target=_run, args=(model_id, load), name="model-download", daemon=True).start()
    return True


def _run(model_id: str, load: Callable[[], object]) -> None:
    try:
        load()
    except Exception as exc:
        offline = is_offline(exc)
        logger.warning(
            "Download of the speech model %s failed%s", model_id, " (offline)" if offline else "", exc_info=True
        )
        with _lock:
            _allowed.discard(model_id)  # the next attempt is the user's click again
            _jobs[model_id] = {
                "state": "error",
                "error": OFFLINE_MESSAGE if offline else f"The download failed: {exc}",
                "offline": offline,
            }
        return
    logger.info("Speech model %s downloaded and loaded", model_id)
    with _lock:
        _jobs[model_id] = {"state": "ready", "error": None, "offline": False}


def is_offline(exc: BaseException) -> bool:
    """Whether `exc`, or an exception it was raised from, says that the network could not be reached."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, socket.gaierror, TimeoutError)):
            return True
        if any(cls.__name__ in _OFFLINE_NAMES for cls in type(current).__mro__):
            return True
        current = current.__cause__ or current.__context__
    return False


def _hub_cache() -> Path:
    """The Hugging Face hub cache the engines download into (the models folder when frozen)."""
    folder = models_dir()
    if folder is not None:
        return folder
    env = os.environ.get("HF_HUB_CACHE")
    if env:
        return Path(env)
    home = os.environ.get("HF_HOME")
    return Path(home) / "hub" if home else Path.home() / ".cache" / "huggingface" / "hub"


def downloaded_bytes(model_id: str) -> int:
    """How many bytes of the model's repository are in the cache so far, partial files included."""
    info = MODEL_REGISTRY.get(model_id, {})
    repo = info.get("download_repo") or info.get("hf_name")
    if not repo:
        return 0
    blobs = _hub_cache() / ("models--" + str(repo).replace("/", "--")) / "blobs"
    total = 0
    try:
        for entry in os.scandir(blobs):
            try:
                total += entry.stat().st_size if entry.is_file() else 0
            except OSError:
                continue
    except OSError:
        return 0
    return total


def progress(model_id: str) -> int | None:
    """Percent of the download that is on disk (0-99 while it runs); None when the expected size is unknown."""
    expected = MODEL_REGISTRY.get(model_id, {}).get("download_bytes")
    if not isinstance(expected, int) or expected <= 0:
        return None
    return min(99, int(downloaded_bytes(model_id) * 100 / expected))


def _reset() -> None:
    """Forget every permission and job (tests)."""
    with _lock:
        _allowed.clear()
        _jobs.clear()
