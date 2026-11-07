"""The object behind the dashboard: the Flask routes in `routes_*` call its methods.

Each topic is a mixin in its own `api_*` module; `DashboardAPI` joins them and owns the state they all use,
and `api_base.DashboardState` declares what they use of each other.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from ..audio.meter import LevelMeter
from ..config import Config
from ..extensions import ExtensionRegistry
from ..meetings.live import LiveTranscriber, WindowTranscriber
from ..meetings.session import MeetingSession
from ..storage.db import Database
from ..transcription.transcriber import BaseTranscriber
from .api_analytics import AnalyticsMixin
from .api_commands import CommandsMixin
from .api_files import FilesMixin
from .api_meeting_transcript import MeetingTranscriptMixin
from .api_meetings import MeetingsMixin
from .api_models import ModelsMixin
from .api_review import ReviewMixin
from .api_settings import SettingsMixin
from .api_setup import SetupMixin
from .api_vocabulary import VocabularyMixin
from .file_jobs import FileJobs


class DashboardAPI(
    ModelsMixin,
    SettingsMixin,
    SetupMixin,
    CommandsMixin,
    AnalyticsMixin,
    ReviewMixin,
    VocabularyMixin,
    FilesMixin,
    MeetingsMixin,
    MeetingTranscriptMixin,
):
    """Everything the dashboard's routes call, joined from the topic mixins."""

    def __init__(
        self,
        session_factory: sessionmaker,
        config: Config,
        db: Database | None = None,
        on_model_switch: Callable[[str], None] | None = None,
        get_switch_status: Callable[[], dict] | None = None,
        get_learn_status: Callable[[], dict] | None = None,
        get_transcriber: Callable[[], BaseTranscriber] | None = None,
        on_feedback_mode: Callable[[str], None] | None = None,
        benchmarks_dir: Path | None = None,
        extensions: ExtensionRegistry | None = None,
        meeting: MeetingSession | None = None,
    ) -> None:
        self._sf = session_factory
        self._config = config
        self._db = db
        self._on_model_switch = on_model_switch
        self._get_switch_status = get_switch_status
        self._get_learn_status = get_learn_status
        self._get_transcriber = get_transcriber
        self._on_feedback_mode = on_feedback_mode
        self._benchmarks_dir = benchmarks_dir or Path("benchmarks")
        self._file_jobs = FileJobs()
        self._level_meter = LevelMeter()
        self._download_cache: dict[str, tuple[float, dict]] = {}
        self._llm_server_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}
        self.extensions = extensions if extensions is not None else ExtensionRegistry()
        self._meeting = meeting or MeetingSession(
            config.meeting,
            self._meetings_dir,
            live_factory=lambda: LiveTranscriber(
                WindowTranscriber(self._make_live_transcriber), labels=self._live_labels
            ),
        )
        # The dashboard and the hotkey both start and stop meetings: one at a time, and a press that arrives
        # while another is being handled sees its outcome.
        self._meeting_lock = threading.RLock()
