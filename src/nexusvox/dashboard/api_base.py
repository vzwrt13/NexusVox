"""Dashboard API: what the topic mixins share."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..config import Config
    from ..extensions import ExtensionRegistry
    from ..meetings.session import MeetingSession
    from ..storage.db import Database
    from ..transcription.transcriber import BaseTranscriber
    from .file_jobs import FileJobs

# All of DashboardAPI logs under one name, the one its log lines have always carried.
logger = logging.getLogger("nexusvox.dashboard.api")


class DashboardState:
    """What a topic mixin may use without defining it, so that the split stays checkable.

    The names are declarations only. No type checker runs on this code, so `tests/test_dashboard_api_contract.py`
    holds it true: it fails when a mixin uses a name that is not declared here, and when a name here no longer exists
    or nothing uses it any more.
    """

    # Set by `DashboardAPI.__init__`.
    _sf: sessionmaker
    _config: Config
    _db: Database | None
    _on_model_switch: Callable[[str], None] | None
    _get_switch_status: Callable[[], dict] | None
    _get_learn_status: Callable[[], dict] | None
    _get_transcriber: Callable[[], BaseTranscriber] | None
    _on_feedback_mode: Callable[[str], None] | None
    _benchmarks_dir: Path
    _file_jobs: FileJobs
    _download_cache: dict[str, tuple[float, dict]]
    _llm_server_cache: dict[tuple[str, str, str], tuple[float, dict]]
    extensions: ExtensionRegistry
    _meeting: MeetingSession
    _meeting_lock: threading.RLock

    # Defined by `SettingsMixin`.
    get_settings: Callable[[], dict]
    # The report the Feedback tab last previewed: (token, (kind, message, include_log), url, report).
    _feedback_preview: tuple[str, tuple, str, dict[str, str]] | None = None

    # Defined by `FilesMixin`.
    _store_file_transcription: Callable[..., dict]
    _transcribe_wav_chunks: Callable[..., Any]

    # Defined by `MeetingsMixin`.
    _meetings_dir: Callable[[], Path]
    _meeting_folder: Callable[[str], Path | None]
    _remove_meeting_files: Callable[[Any], None]

    # Defined by `MeetingTranscriptMixin`.
    start_meeting_transcription: Callable[[str | Path], dict]

    def _db_dir(self) -> Path:
        """The folder of the database file, which recordings' relative `audio_path`s are relative to."""
        return Path(self._config.database.path).resolve().parent
