"""The one meeting recording a running app can have: start, stop and what the dashboard shows meanwhile."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .config import MeetingConfig
from .meeting_recorder import LoopbackSource, MeetingRecorder, MicSource

logger = logging.getLogger(__name__)


class MeetingSession:
    """Thread-safe start/stop around `MeetingRecorder`, shared by the dashboard and the meeting hotkey.

    The recorder is built per meeting from the current `[meeting]` devices, so a device chosen in the
    dashboard applies to the next recording.
    """

    def __init__(
        self,
        config: MeetingConfig,
        base_dir: Callable[[], Path],
        recorder_factory: Callable[[Path, MeetingConfig], MeetingRecorder] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._base_dir = base_dir
        self._factory = recorder_factory or self._default_recorder
        self._clock = clock
        self._lock = threading.Lock()
        self._recorder: MeetingRecorder | None = None
        self._started: float | None = None

    @staticmethod
    def _default_recorder(base_dir: Path, config: MeetingConfig) -> MeetingRecorder:
        return MeetingRecorder(base_dir, MicSource(config.mic_device), LoopbackSource(config.output_device))

    @property
    def recording(self) -> bool:
        return self._recorder is not None

    def start(self) -> Path:
        """Begin a meeting; raises when one runs already or a device cannot be opened."""
        with self._lock:
            if self._recorder is not None:
                raise RuntimeError("A meeting is already being recorded")
            recorder = self._factory(self._base_dir(), self._config)
            folder = recorder.start()
            self._recorder = recorder
            self._started = self._clock()
            return folder

    def stop(self) -> Path:
        """End the meeting and return its folder. The session is free again even when closing a track failed."""
        with self._lock:
            if self._recorder is None:
                raise RuntimeError("No meeting is being recorded")
            recorder, self._recorder, self._started = self._recorder, None, None
            return recorder.stop()

    def add_dictation(self, start: float, end: float) -> None:
        """Hand a dictation take (`time.monotonic()` values) to the running meeting; nothing when none runs."""
        with self._lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.add_dictation(start, end)

    def status(self) -> dict:
        """`{recording, elapsed_s, folder}` for the dashboard's timer."""
        with self._lock:
            if self._recorder is None or self._started is None:
                return {"recording": False, "elapsed_s": 0, "folder": None}
            folder = self._recorder.folder
            return {
                "recording": True,
                "elapsed_s": int(self._clock() - self._started),
                "folder": folder.name if folder else None,
            }
