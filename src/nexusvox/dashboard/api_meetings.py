"""Dashboard API: recording meetings, their settings and the recordings still waiting for a transcript."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..config import (
    HOTKEY_MODIFIERS,
    normalize_hotkey_key,
    normalize_speaker_label,
    own_voice_label_problem,
    save_config,
    validate_meeting_hotkey,
)
from ..meeting_live import LIVE_OTHERS_LABEL
from ..meeting_recorder import META_FILENAME, list_devices
from ..transcriber import BaseTranscriber, create_transcriber

# All of DashboardAPI logs under one name, the one its log lines have always carried.
logger = logging.getLogger("nexusvox.dashboard.api")


def _remove_folder(folder: Path) -> None:
    """Delete a meeting folder so that a failure can be retried: `meeting.json` goes last, because it is what
    marks the folder as a meeting. A file that is locked (a player, a virus scanner) raises after the others
    are gone and the folder keeps its `meeting.json`."""
    first_error: OSError | None = None
    for entry in sorted(folder.iterdir(), key=lambda p: p.name == META_FILENAME):
        if entry.name == META_FILENAME and first_error is not None:
            break
        try:
            entry.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    folder.rmdir()


class MeetingsMixin:
    """Start, stop and the Meetings tab's lists and settings; the transcript is `MeetingTranscriptMixin`."""

    def _meetings_dir(self) -> Path:
        """Where each meeting gets its own folder: `meetings/` inside the audio directory."""
        return self._db_dir() / self._config.database.audio_dir / "meetings"

    def get_meeting_status(self) -> dict:
        """Whether a meeting is being recorded and for how long (the dashboard's timer)."""
        return self._meeting.status()

    def start_meeting(self) -> dict:
        with self._meeting_lock:
            try:
                folder = self._meeting.start()
            except Exception as exc:
                logger.error("Starting the meeting recording failed: %s", exc)
                return {"ok": False, "error": f"Could not start the recording: {exc}"}
            return {**self._meeting.status(), "ok": True, "folder": folder.name}

    def stop_meeting(self) -> dict:
        """End the recording and start transcribing it. `busy` means another file is still being transcribed:
        the recording is safe in `folder` and is listed as waiting for its transcription (`get_pending_meetings`)."""
        with self._meeting_lock:
            try:
                folder = self._meeting.stop()
            except Exception as exc:
                logger.error("Stopping the meeting recording failed: %s", exc)
                return {"ok": False, "error": f"Could not stop the recording: {exc}"}
            job = self.start_meeting_transcription(folder)
            if job.get("busy"):
                job["error"] = "Recording saved, but another file is still being transcribed"
            return {**job, "folder": folder.name, "recording": False}

    def toggle_meeting(self, *, ask_consent: bool = False) -> dict:
        """Start a meeting, or stop the running one (the meeting hotkey). `action` says what happened: `stop`,
        `start`, or `consent` when the hint is on and `ask_consent` is set: nothing was started, the dashboard
        has to show the hint first. A stopped meeting that could not be transcribed yet is still `ok` for the
        hotkey (`busy`): the recording itself ended."""
        with self._meeting_lock:
            if self._meeting.recording:
                return {**self.stop_meeting(), "action": "stop"}
            if ask_consent and self._config.meeting.consent_hint:
                return {"ok": True, "action": "consent"}
            return {**self.start_meeting(), "action": "start"}

    def _meeting_folder(self, name: str) -> Path | None:
        """The folder of a recorded meeting by name, None when the name is not a meeting folder."""
        if not re.fullmatch(r"meeting-[0-9A-Za-z-]+", name or ""):
            return None
        folder = self._meetings_dir() / name
        return folder if (folder / META_FILENAME).is_file() else None

    def get_pending_meetings(self) -> list[dict]:
        """Recorded meetings without a transcript: the transcriber was busy, the app quit mid-meeting or the
        transcription failed. Not the meeting being recorded or transcribed right now. Newest first."""
        base = self._meetings_dir()
        if self._db is None or not base.is_dir():
            return []
        skip = self._db.meeting_folder_names() | {
            self._meeting.status().get("folder"),
            self._file_jobs.running_filename(),
        }
        pending = []
        for folder in base.iterdir():
            if folder.name in skip or self._meeting_folder(folder.name) is None:
                continue
            try:
                started = json.loads((folder / META_FILENAME).read_text(encoding="utf-8")).get("started_at")
            except (OSError, ValueError):
                started = None
            pending.append({"name": folder.name, "started_at": started})
        return sorted(pending, key=lambda p: p["name"], reverse=True)

    def transcribe_pending_meeting(self, name: str) -> dict:
        folder = self._meeting_folder(name)
        if folder is None or name not in {p["name"] for p in self.get_pending_meetings()}:
            return {"ok": False, "error": "Unknown meeting"}
        return self.start_meeting_transcription(folder)

    def delete_pending_meeting(self, name: str) -> dict:
        folder = self._meeting_folder(name)
        if folder is None or name not in {p["name"] for p in self.get_pending_meetings()}:
            return {"ok": False, "error": "Unknown meeting"}
        try:
            _remove_folder(folder)
        except OSError as exc:
            logger.error("Deleting meeting folder %s failed: %s", folder, exc)
            return {"ok": False, "error": f"Could not delete the audio files: {exc}"}
        return {"ok": True}

    def get_meeting_devices(self) -> dict:
        """Input and output device names to choose from, plus the chosen ones (empty: the default device)."""
        return {
            **list_devices(),
            "mic_device": self._config.meeting.mic_device,
            "output_device": self._config.meeting.output_device,
        }

    def set_meeting_hotkey(self, modifiers: list, key: str) -> dict:
        """Change the meeting hotkey. Live: the listener reads the shared config on every key event."""
        modifiers = [str(m).lower() for m in modifiers]
        key = str(key or "")
        error = validate_meeting_hotkey(modifiers, key, self._config.hotkey)
        if error is not None:
            return {"error": error, **self.get_settings()}
        self._config.meeting.modifiers = [m for m in HOTKEY_MODIFIERS if m in modifiers]
        self._config.meeting.key = normalize_hotkey_key(key) or ""
        save_config(self._config)
        return self.get_settings()

    def set_meeting_devices(self, mic: str, output: str) -> dict:
        """Used from the next meeting on; an empty name is the system default device."""
        self._config.meeting.mic_device = str(mic or "")
        self._config.meeting.output_device = str(output or "")
        save_config(self._config)
        return self.get_settings()

    def _set_meeting_flag(self, name: str, enabled: bool) -> dict:
        setattr(self._config.meeting, name, bool(enabled))
        save_config(self._config)
        return self.get_settings()

    def set_meeting_consent_hint(self, enabled: bool) -> dict:
        return self._set_meeting_flag("consent_hint", enabled)

    def set_meeting_own_voice_label(self, label: str) -> dict:
        """The name of the microphone's speaker in meeting transcripts; applied when a meeting is transcribed."""
        label = normalize_speaker_label(label)
        problem = own_voice_label_problem(label)
        if problem:
            return {"error": problem, **self.get_settings()}
        self._config.meeting.own_voice_label = label
        save_config(self._config)
        return self.get_settings()

    def set_meeting_leave_dictation_out(self, enabled: bool) -> dict:
        """Applied when a meeting is transcribed, so it also covers a meeting recorded earlier."""
        return self._set_meeting_flag("leave_dictation_out", enabled)

    def set_meeting_store_audio(self, enabled: bool) -> dict:
        """Applied when a meeting is transcribed; audio of meetings already stored stays."""
        return self._set_meeting_flag("store_audio", enabled)

    def set_meeting_live_transcript(self, enabled: bool) -> dict:
        """Applied from the next meeting on; the running one keeps what it started with."""
        return self._set_meeting_flag("live_transcript", enabled)

    def get_meeting_live(self, after: int = 0) -> dict:
        """Live transcript segments from `after` on (the Meetings tab polls this while a meeting records)."""
        return self._meeting.live(after)

    def _live_labels(self) -> dict[str, str]:
        """Live speaker labels: the microphone is named after the Settings label, everyone on the system track is
        `Others` (no speaker detection while recording; the stored transcript numbers them)."""
        return {
            "mic": self._config.meeting.own_voice_label,
            "system": LIVE_OTHERS_LABEL,
        }

    def _make_live_transcriber(self) -> BaseTranscriber:
        """The transcriber one live transcript uses for all its windows (current model)."""
        return create_transcriber(
            self._config.inference,
            device=None,
            auto_fallback=self._config.inference.device == "auto",
        )

    def begin_dictation(self, start: float) -> int | None:
        """A dictation take began (`time.monotonic()`); the token for `end_dictation`. Never raises."""
        try:
            return self._meeting.begin_dictation(start)
        except Exception:
            logger.exception("Storing the dictation range of the meeting failed")
            return None

    def end_dictation(self, token: int | None, start: float, end: float) -> None:
        """A dictation take ended; the running meeting stores its time range. Never raises."""
        try:
            self._meeting.end_dictation(token, start, end)
        except Exception:
            logger.exception("Storing the dictation range of the meeting failed")

    def shutdown_meeting(self) -> None:
        """Close a running recording when the app quits, so its WAV files get proper headers."""
        if self._meeting.recording:
            try:
                self._meeting.stop()
            except Exception:
                logger.exception("Closing the meeting recording on exit failed")

    def _remove_meeting_files(self, record) -> None:
        """Remove a meeting's folder: found next to its mix, else by name in the meetings directory.
        Only a folder named `meeting-*` that holds `meeting.json` is ever removed."""
        candidates = [self._meetings_dir() / record.original_filename]
        if record.audio_path:
            candidates.insert(0, (self._db_dir() / record.audio_path).parent)
        for folder in candidates:
            if folder.name.startswith("meeting-") and (folder / META_FILENAME).is_file():
                _remove_folder(folder)
                return
