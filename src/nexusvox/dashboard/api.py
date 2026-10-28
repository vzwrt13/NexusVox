"""Dashboard API exposed to JavaScript via the pywebview JS bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from ..config import (
    CLEANUP_LEVELS,
    CUSTOM_PROTOCOLS,
    FEEDBACK_MODES,
    HOTKEY_KEY_VKS,
    HOTKEY_MODES,
    HOTKEY_MODIFIERS,
    MODEL_REGISTRY,
    SUPPORTED_LANGUAGES,
    Config,
    HotkeyConfig,
    MeetingConfig,
    meeting_hotkey_clash,
    normalize_hotkey_key,
    normalize_speaker_label,
    numbered_speaker_label,
    own_voice_label_problem,
    resolve_device,
    save_config,
    validate_hotkey,
    validate_meeting_hotkey,
)
from ..db import Database
from ..diarization import speaker_detection_models
from ..dictionary import suggest_entries
from ..edit_watch import WATCHED_KEYS
from ..exports import NO_TIMING_HINT, SUBTITLE_TARGET_IDS
from ..extensions import HOOK_TIMEOUT_S, ExtensionRegistry, file_transcript_from_record
from ..file_cleanup import clean_transcript
from ..file_transcribe import (
    ALLOWED_EXTENSIONS,
    MAX_CHUNK_MS,
    MAX_UPLOAD_BYTES,
    NO_SPEAKER_LABELS,
    convert_to_wav,
    transcribe_file,
)
from ..llm import is_reachable as llm_reachable
from ..llm import warm_up as llm_warm_up
from ..logsetup import log_dir
from ..meeting_live import LIVE_OTHERS_LABEL, LiveTranscriber, WindowTranscriber
from ..meeting_recorder import META_FILENAME, list_devices
from ..meeting_session import MeetingSession
from ..meeting_transcribe import (
    MAX_DETECTION_S,
    MIX_FILENAME,
    SPEAKER_FIRST_SYSTEM,
    SPEAKER_OWN,
    TRACK_NAMES,
    convert_track,
    format_meeting,
    load_meeting,
    merge_words,
    mix_tracks,
    track_offset,
    track_shifts,
)
from ..os_commands import NEXUS_ACTIONS
from ..polish_presets import PolishPreset
from ..snippets import Snippet, SnippetOption
from ..transcriber import BaseTranscriber, create_transcriber
from ..translator import is_reachable
from ..voice_commands import ALL_STRUCTURAL_INFO, ALL_STRUCTURAL_KEYWORDS, ALL_SYMBOL_INFO
from . import analytics
from . import benchmarks as bench
from .file_jobs import FileJob, FileJobs

logger = logging.getLogger(__name__)


# `[hotkey]` fields that hold a marker key, and how an error message names each.
_MARKER_KEY_FIELDS = {"paste_key": "paste", "polish_key": "polish"}


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


class DashboardAPI:
    """Methods callable from JavaScript as ``pywebview.api.<method>(...)``."""

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

    # ---- Models -------------------------------------------------------------

    def get_available_models(self) -> list[dict]:
        resolved = resolve_device(self._config.inference.device)
        custom = self._config.inference.custom
        return [
            {
                "id": model_id,
                "name": info["display_name"],
                # A custom server reports the protocol it is configured to speak.
                "protocol": CUSTOM_PROTOCOLS[custom.protocol] if info.get("custom") else info["protocol"],
                "custom": bool(info.get("custom", False)),
                "server_url": custom.url if info.get("custom") else "",
                "description": info.get("description", ""),
                "parameters": info.get("parameters", "—"),
                "architecture": info.get("architecture", "—"),
                "languages": info.get("languages", "—"),
                "streaming": custom.protocol == "realtime"
                if info.get("custom")
                else info.get("streaming", "false") == "true",
                "vram_gb": info.get("vram_gb", "—"),
                "hf_name": info["hf_name"],
                "requires_gpu": bool(info.get("requires_gpu", True)),
                "inprocess_supported": bool(info.get("inprocess_supported", False)),
            }
            for model_id, info in MODEL_REGISTRY.items()
            if not (resolved == "cpu" and info.get("requires_gpu"))
        ]

    def get_current_model(self) -> dict:
        model = self._config.inference.model
        flagged = bool(MODEL_REGISTRY.get(model, {}).get("diarization"))
        # Whether the engine that is running can do it: Parakeet's speaker detection is part of its Docker
        # container, so the in-process CPU engine cannot. Without a running app, assume the container.
        transcriber = self._get_transcriber() if self._get_transcriber else None
        engine_can = transcriber.supports_diarization if transcriber else True
        # `diarization`: the upload tab's speaker detection works with this model; `diarization_models`
        # names the models it works with, and `diarization_hint` says why it is off for a model that has it.
        return {
            "model": model,
            "diarization": flagged and engine_can,
            "diarization_models": speaker_detection_models(),
            "diarization_hint": transcriber.no_diarization_reason if flagged and not engine_can else "",
        }

    def set_model(self, model_id: str) -> dict:
        if model_id not in MODEL_REGISTRY:
            return {"ok": False, "error": f"Unknown model: {model_id}"}
        info = MODEL_REGISTRY[model_id]
        resolved = resolve_device(self._config.inference.device)
        if resolved == "cpu" and info.get("requires_gpu"):
            return {
                "ok": False,
                "error": f"Model {model_id} requires a GPU and is not available on CPU.",
            }
        if self._on_model_switch:
            self._on_model_switch(model_id)
            return {"ok": True, "status": "switching"}
        # No callback — update config directly (fallback for tests / no app)
        self._config.inference.model = model_id
        default_url = info.get("default_url")
        if default_url:
            self._config.inference.server_url = str(default_url)
        save_config(self._config)
        return {"ok": True, "model": model_id}

    def get_model_status(self) -> dict:
        if self._get_switch_status:
            return self._get_switch_status()
        return {"status": "idle"}

    # ---- Device ------------------------------------------------------------

    def get_device(self) -> dict:
        requested = self._config.inference.device
        return {
            "requested": requested,
            "resolved": resolve_device(requested),
            "cuda_available": resolve_device("auto") == "cuda",
        }

    def set_device(self, requested: str) -> dict:
        if requested not in ("auto", "cuda", "cpu"):
            return {"ok": False, "error": "device must be 'auto', 'cuda', or 'cpu'"}
        self._config.inference.device = requested
        save_config(self._config)
        return {
            "ok": True,
            "requires_restart": True,
            **self.get_device(),
        }

    # ---- Settings ----------------------------------------------------------

    def get_settings(self) -> dict:
        return {
            "auto_language_detection": self._config.auto_language_detection,
            "language": self._config.language,
            "translate_enabled": self._config.translator.enabled,
            "translator_url": self._config.translator.url,
            "hotkey_mode": self._config.hotkey.mode,
            "hotkey_modifiers": list(self._config.hotkey.modifiers),
            "hotkey_key": self._config.hotkey.key,
            "paste_key": self._config.hotkey.paste_key,
            "polish_key": self._config.hotkey.polish_key,
            "llm_model": self._config.llm.model,
            "hotkey_default": {"modifiers": list(HotkeyConfig().modifiers), "key": HotkeyConfig().key},
            "hotkey_modifier_options": list(HOTKEY_MODIFIERS),
            "hotkey_key_options": list(HOTKEY_KEY_VKS),
            "mic_guard_enabled": self._config.mic_guard.enabled,
            "feedback_mode": self._config.feedback.mode,
            "feedback_modes": list(FEEDBACK_MODES),
            "store_audio": self._config.database.store_audio,
            "log_full_text": self._config.log_full_text,
            "log_dir": str(log_dir()),
            "learn_from_edits": self._config.learn_from_edits.enabled,
            "polish_in_terminals": self._config.polish.in_terminals,
            "cleanup_level": self._config.cleanup.level,
            "cleanup_levels": list(CLEANUP_LEVELS),
            "meeting_modifiers": list(self._config.meeting.modifiers),
            "meeting_key": self._config.meeting.key,
            "meeting_hotkey_default": {"modifiers": list(MeetingConfig().modifiers), "key": MeetingConfig().key},
            "meeting_mic_device": self._config.meeting.mic_device,
            "meeting_output_device": self._config.meeting.output_device,
            "meeting_consent_hint": self._config.meeting.consent_hint,
            "meeting_leave_dictation_out": self._config.meeting.leave_dictation_out,
            "meeting_own_voice_label": self._config.meeting.own_voice_label,
            "meeting_live_transcript": self._config.meeting.live_transcript,
        }

    def get_translator_status(self) -> dict:
        """Whether the external translator server answers - probed live, so the
        dashboard can say when the translate toggle would have no effect."""
        return {
            "url": self._config.translator.url,
            "reachable": is_reachable(self._config.translator),
        }

    def set_auto_language_detection(self, enabled: bool) -> dict:
        self._config.auto_language_detection = enabled
        save_config(self._config)
        return self.get_settings()

    def set_language(self, language: str) -> dict:
        if language not in SUPPORTED_LANGUAGES:
            return {"error": f"Unsupported language: {language}", **self.get_settings()}
        self._config.language = language
        save_config(self._config)
        return self.get_settings()

    def set_hotkey_mode(self, mode: str) -> dict:
        """The listener reads the shared config on every key event, so this is live."""
        if mode not in HOTKEY_MODES:
            return {"error": f"Unsupported hotkey mode: {mode}", **self.get_settings()}
        self._config.hotkey.mode = mode
        save_config(self._config)
        return self.get_settings()

    def set_hotkey(self, modifiers: list, key: str) -> dict:
        """Change the key binding. Live as well: the listener re-reads it on the next key event."""
        modifiers = [str(m).lower() for m in modifiers]
        key = str(key or "")
        error = validate_hotkey(modifiers, key)
        if error is None and meeting_hotkey_clash(
            self._config.meeting.modifiers,
            self._config.meeting.key,
            HotkeyConfig(modifiers=modifiers, key=normalize_hotkey_key(key) or ""),
        ):
            error = "The meeting hotkey would also start dictation with that; change the meeting hotkey first"
        if error is not None:
            return {"error": error, **self.get_settings()}
        # Keep the canonical order so the config file and the hint read the same everywhere.
        self._config.hotkey.modifiers = [m for m in HOTKEY_MODIFIERS if m in modifiers]
        self._config.hotkey.key = normalize_hotkey_key(key) or ""
        save_config(self._config)
        return self.get_settings()

    def _set_marker_key(self, field: str, key: str) -> dict:
        """Set one of `_MARKER_KEY_FIELDS`. Live: the listener reads the config on every key event."""
        name = normalize_hotkey_key(str(key or ""))
        if name is None:
            return {"error": f"Unknown key: {key}", **self.get_settings()}
        # The listener would silently ignore or misread these: the binding's own key is never
        # a marker, the os-control command key wins a clash, and two markers cannot share a key.
        hotkey = self._config.hotkey
        if name and name == hotkey.key:
            return {"error": f"{name} already completes the hotkey", **self.get_settings()}
        if name and self._config.os_control.enabled and name == self._config.os_control.command_key:
            return {"error": f"{name} is the os-control command key", **self.get_settings()}
        for other, label in _MARKER_KEY_FIELDS.items():
            if name and other != field and name == getattr(hotkey, other):
                return {"error": f"{name} is already the {label} key", **self.get_settings()}
        setattr(hotkey, field, name)
        save_config(self._config)
        return self.get_settings()

    def set_paste_key(self, key: str) -> dict:
        return self._set_marker_key("paste_key", key)

    def set_polish_key(self, key: str) -> dict:
        return self._set_marker_key("polish_key", key)

    def set_llm_model(self, model: str) -> dict:
        """The model name polish asks the [llm] server for (e.g. "qwen3.5:9b"). Read per request, so live."""
        model = str(model or "").strip()
        if not model:
            return {"error": "A model name is required", **self.get_settings()}
        self._config.llm.model = model
        save_config(self._config)
        if self._config.cleanup.level != "off":
            llm_warm_up(self._config.llm)  # cleanup's short timeout would skip dictations while it loads
        return self.get_settings()

    def get_llm_status(self) -> dict:
        """Whether the [llm] server answers - probed live, like the translator."""
        return {
            "url": self._config.llm.url,
            "model": self._config.llm.model,
            "reachable": llm_reachable(self._config.llm),
        }

    def set_mic_guard_enabled(self, enabled: bool) -> dict:
        """Checked by the app on every hold, so this is live."""
        self._config.mic_guard.enabled = enabled
        save_config(self._config)
        return self.get_settings()

    def set_feedback_mode(self, mode: str) -> dict:
        """Read by the app on every cue, so this is live."""
        if mode not in FEEDBACK_MODES:
            return {"error": f"Unsupported feedback mode: {mode}", **self.get_settings()}
        self._config.feedback.mode = mode
        save_config(self._config)
        if self._on_feedback_mode:
            self._on_feedback_mode(mode)  # builds the overlay window now, not in the middle of a dictation
        return self.get_settings()

    def set_cleanup_level(self, level: str) -> dict:
        """Read by the pipeline on every dictation, so this is live."""
        if level not in CLEANUP_LEVELS:
            return {"error": f"Unsupported cleanup level: {level}", **self.get_settings()}
        self._config.cleanup.level = level
        save_config(self._config)
        if level != "off":
            llm_warm_up(self._config.llm)  # the first cleaned dictation should not wait for the model
        return self.get_settings()

    def set_polish_in_terminals(self, enabled: bool) -> dict:
        """Read by the app on every polish, so this is live."""
        self._config.polish.in_terminals = enabled
        save_config(self._config)
        return self.get_settings()

    def set_learn_from_edits(self, enabled: bool) -> dict:
        """Checked by the app after every paste, so this is live."""
        self._config.learn_from_edits.enabled = enabled
        save_config(self._config)
        return self.get_settings()

    def get_learn_status(self) -> dict:
        """How the pastes since start ended for learning from edits (`EditWatcher.status`)."""
        if self._get_learn_status is None:
            return {"counts": {}, "watched": list(WATCHED_KEYS), "last": None}
        return self._get_learn_status()

    def set_log_full_text(self, enabled: bool) -> dict:
        """The file log's filter reads the config on every record, so this is live."""
        self._config.log_full_text = enabled
        save_config(self._config)
        return self.get_settings()

    def open_log_folder(self) -> dict:
        """Opens the log folder in Explorer, creating it first when nothing has been logged yet."""
        folder = log_dir()
        startfile = getattr(os, "startfile", None)  # Windows only; missing on a development machine elsewhere
        if startfile is None:
            return {"error": f"Opening a folder is only supported on Windows; the log folder is {folder}"}
        try:
            folder.mkdir(parents=True, exist_ok=True)
            startfile(folder)
        except OSError as exc:
            logger.error("Could not open the log folder %s: %s", folder, exc)
            return {"error": f"Could not open {folder}: {exc}"}
        return {"ok": True, "path": str(folder)}

    def set_store_audio(self, enabled: bool) -> dict:
        """Checked by the app on every recording, so this is live."""
        self._config.database.store_audio = enabled
        save_config(self._config)
        return self.get_settings()

    def set_translate_enabled(self, enabled: bool) -> dict:
        self._config.translator.enabled = enabled
        save_config(self._config)
        return self.get_settings()

    # ---- Voice Commands ----------------------------------------------------

    def get_voice_commands(self) -> dict:
        return {
            "enabled": self._config.voice_commands.enabled,
            "numbers_as_digits": self._config.voice_commands.numbers_as_digits,
            "bypass_symbols": self._config.voice_commands.bypass_symbols,
            "symbols": self._config.voice_commands.symbols,
            "all_symbols": ALL_SYMBOL_INFO,
            "bypass_structural": self._config.voice_commands.bypass_structural,
            "structural": self._config.voice_commands.structural,
            "all_structural": ALL_STRUCTURAL_INFO,
            "press_enter": self._config.voice_commands.press_enter,
        }

    def set_voice_commands_enabled(self, enabled: bool) -> dict:
        self._config.voice_commands.enabled = enabled
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_numbers(self, enabled: bool) -> dict:
        self._config.voice_commands.numbers_as_digits = enabled
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_press_enter(self, enabled: bool) -> dict:
        self._config.voice_commands.press_enter = enabled
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_symbols(self, symbols: list[str]) -> dict:
        self._config.voice_commands.symbols = symbols
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_bypass_symbols(self, enabled: bool) -> dict:
        self._config.voice_commands.bypass_symbols = enabled
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_structural(self, structural: list[str]) -> dict:
        self._config.voice_commands.structural = [k for k in structural if k in ALL_STRUCTURAL_KEYWORDS]
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_bypass_structural(self, enabled: bool) -> dict:
        self._config.voice_commands.bypass_structural = enabled
        save_config(self._config)
        return self.get_voice_commands()

    # ---- OS Commands -------------------------------------------------------

    def get_os_commands(self) -> dict:
        return {
            "enabled": self._config.os_commands.enabled,
            "apps": self._config.os_commands.apps,
            "supported_actions": NEXUS_ACTIONS,
        }

    def set_os_commands_enabled(self, enabled: bool) -> dict:
        self._config.os_commands.enabled = enabled
        save_config(self._config)
        return self.get_os_commands()

    def set_os_commands_apps(self, apps: dict[str, str]) -> dict:
        self._config.os_commands.apps = apps
        save_config(self._config)
        return self.get_os_commands()

    # ---- Analytics ---------------------------------------------------------

    def get_overview(self, start: str | None = None, end: str | None = None) -> dict:
        return {
            **analytics.get_overview(self._sf, start=start, end=end),
            **analytics.get_upload_overview(self._sf, start=start, end=end),
        }

    def get_transcriptions_over_time(
        self, period: str = "day", start: str | None = None, end: str | None = None
    ) -> dict:
        return analytics.get_transcriptions_over_time(self._sf, period, start=start, end=end)

    def get_language_distribution(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_language_distribution(self._sf, start=start, end=end)

    def get_top_words(self, n: int = 20, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_top_words(self._sf, n, start=start, end=end)

    def get_peak_usage_hours(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_peak_usage_hours(self._sf, start=start, end=end)

    def get_activity_heatmap(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_activity_heatmap(self._sf, start=start, end=end)

    # ---- Flagged / Corrections ---------------------------------------------

    def get_flagged_transcriptions(self) -> list[dict]:
        return analytics.get_flagged_transcriptions(self._sf)

    def get_audio_file_path(self, tid: int) -> str | None:
        """Resolve the absolute path to a transcription's audio file."""
        if self._db is None:
            return None
        from ..models import Transcription

        with self._sf() as session:
            row = session.get(Transcription, tid)
            if row and row.audio_path:
                return str(self._db_dir() / row.audio_path)
        return None

    def retry_transcription(self, tid: int) -> dict:
        """Transcribe a recording's stored WAV again with the current model. Saves nothing."""
        path = self.get_audio_file_path(tid)
        if path is None:
            return {"ok": False, "error": "No stored audio for this recording - turn on Store Audio Recordings"}
        if not Path(path).is_file():
            return {"ok": False, "error": "The stored audio file for this recording is missing"}
        try:
            wav_chunks, _ = convert_to_wav(Path(path).read_bytes(), "recording.wav")
            result = self._transcribe_wav_chunks(wav_chunks)
        except Exception as exc:
            logger.error("Retry failed for transcription %d: %s", tid, exc)
            return {"ok": False, "error": f"Transcription failed: {exc}"}
        if result.notes:
            return {"ok": False, "error": "Transcription incomplete: " + " ".join(result.notes)}
        return {"ok": True, "text": result.text, "model": self._config.inference.model}

    def update_correction(self, tid: int, corrected_text: str) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        ok = self._db.update_correction(tid, corrected_text)
        return {"ok": ok}

    # ---- Review ------------------------------------------------------------

    def get_unreviewed_transcriptions(self, *, before_id: int | None = None) -> dict:
        return analytics.get_unreviewed_transcriptions(self._sf, before_id=before_id)

    def submit_review(self, tid: int, is_correct: bool, corrected_text: str | None = None) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        original = self._db.get_transcription_text(tid)
        ok = self._db.submit_review(tid, is_correct, corrected_text)
        suggestions: list[dict] = []
        if ok and not is_correct and corrected_text and original:
            suggestions = [
                {"wrong": e.wrong, "right": e.right}
                for e in suggest_entries(original, corrected_text, self._db.get_dictionary_entries())
            ]
        return {"ok": ok, "suggestions": suggestions}

    # ---- Correction dictionary ---------------------------------------------

    def get_dictionary(self) -> dict:
        return {"entries": self._db.list_dictionary() if self._db else []}

    def add_dictionary_entry(self, wrong: str, right: str) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        entry = self._db.add_dictionary_entry(wrong, right)
        if entry is None:
            return {"ok": False, "error": "Both fields are required"}
        return {"ok": True, "entry": entry}

    def delete_dictionary_entry(self, entry_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_dictionary_entry(entry_id)}

    # ---- Snippets ----------------------------------------------------------

    def get_snippets(self) -> dict:
        return {"snippets": self._db.list_snippets() if self._db else []}

    def save_snippet(self, trigger: str, expansion: str, snippet_id: int | None, options: list) -> dict:
        """Add a snippet, or change the one with ``snippet_id``. ``options`` is a list of ``{name, text}``."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        snippet = Snippet(
            trigger=trigger,
            expansion=expansion,
            options=tuple(
                SnippetOption(" ".join(str(o.get("name", "")).split()), str(o.get("text", "")).strip())
                for o in options
                if isinstance(o, dict) and str(o.get("name", "")).strip() and str(o.get("text", "")).strip()
            ),
        )
        saved = self._db.add_snippet(snippet) if snippet_id is None else self._db.update_snippet(snippet_id, snippet)
        if saved is None:
            return {"ok": False, "error": "A trigger and a text or option are required, and it must be unique"}
        return {"ok": True, "snippet": saved}

    # ---- Polish presets ----------------------------------------------------

    def get_polish_presets(self) -> dict:
        return {"presets": self._db.list_polish_presets() if self._db else []}

    def save_polish_preset(self, name: str, instruction: str, is_default: bool, preset_id: int | None) -> dict:
        """Add a preset, or change the one with ``preset_id``. Read on every polish, so this is live."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        saved = self._db.save_polish_preset(preset_id, PolishPreset(str(name), str(instruction), bool(is_default)))
        if saved is None:
            return {"ok": False, "error": "A unique name of up to 5 words and an instruction are required"}
        return {"ok": True, "preset": saved}

    def delete_polish_preset(self, preset_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_polish_preset(preset_id)}

    def get_github_repos(self) -> dict:
        """The user's GitHub repositories as snippet options (``{name, text}`` with the URL), via the GitHub CLI."""
        try:
            proc = subprocess.run(
                ["gh", "repo", "list", "--json", "name,url", "--limit", "200"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"GitHub CLI not available: {exc}"}
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr.strip() or "gh repo list failed")[:300]}
        repos = sorted(json.loads(proc.stdout or "[]"), key=lambda r: r["name"].lower())
        return {"ok": True, "options": [{"name": r["name"], "text": r["url"]} for r in repos]}

    def delete_snippet(self, snippet_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_snippet(snippet_id)}

    def get_dictionary_suggestions(self) -> dict:
        return {"suggestions": self._db.list_dictionary_suggestions() if self._db else []}

    def accept_dictionary_suggestion(self, suggestion_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        entry = self._db.accept_dictionary_suggestion(suggestion_id)
        if entry is None:
            return {"ok": False, "error": "Suggestion not found"}
        return {"ok": True, "entry": entry}

    def delete_dictionary_suggestion(self, suggestion_id: int) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        return {"ok": self._db.delete_dictionary_suggestion(suggestion_id)}

    def get_latency_trend(self, period: str = "day", start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_latency_over_time(self._sf, period, start=start, end=end)

    def get_model_breakdown(self, start: str | None = None, end: str | None = None) -> list[dict]:
        return analytics.get_model_breakdown(self._sf, start=start, end=end)

    def get_app_breakdown(self, start: str | None = None, end: str | None = None) -> list[dict]:
        return analytics.get_app_breakdown(self._sf, start=start, end=end)

    # ---- File Upload Transcription -----------------------------------------

    def start_file_transcription(
        self, file_bytes: bytes, filename: str, *, diarize: bool = False, cleanup_level: str = ""
    ) -> dict:
        """Check an uploaded file and transcribe it in the background; `{"ok": True, "job_id"}` at once.

        A `cleanup_level` of `light` or `medium` also runs the transcript through the [llm] model;
        anything else is no cleanup.

        Progress and the outcome are read with `get_file_job` / `get_current_file_job`.
        """
        error = self._check_upload(file_bytes, filename)
        if error:
            return {"ok": False, "error": error}
        job = self._file_jobs.start(
            filename,
            lambda job: self.upload_and_transcribe(
                file_bytes, filename, diarize=diarize, cleanup_level=cleanup_level, job=job
            ),
        )
        if job is None:
            return {"ok": False, "busy": True, "error": "Another file is still being transcribed"}
        return {"ok": True, "job_id": job.id}

    def get_file_job(self, job_id: str) -> dict | None:
        return self._file_jobs.get(job_id)

    def get_current_file_job(self) -> dict | None:
        """The running file job, or the latest one whose ending no client has read yet."""
        return self._file_jobs.current()

    def _check_upload(self, file_bytes: bytes, filename: str) -> str:
        """Why an uploaded file cannot be transcribed, or an empty string."""
        if self._db is None:
            return "DB not available"
        from ..file_transcribe import _get_extension

        ext = _get_extension(filename)
        if ext not in ALLOWED_EXTENSIONS:
            return f"Unsupported format: {ext}"
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            return f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
        return ""

    def upload_and_transcribe(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        diarize: bool = False,
        cleanup_level: str = "",
        job: FileJob | None = None,
    ) -> dict:
        """Convert an uploaded audio file, transcribe it, and save to DB (blocking); the file is already checked.

        With `diarize`, the saved and returned text is labelled by speaker when the model supports it.
        With a `cleanup_level`, the transcript is also cleaned up by the [llm] model; that text is saved
        beside the transcript, never in place of it. `job` receives the stage as the work moves on.
        """
        started = time.monotonic()
        progress = job.update if job else None
        if progress:
            progress("converting")

        try:
            wav_chunks, duration_ms = convert_to_wav(file_bytes, filename)
        except Exception as exc:
            logger.error("Audio conversion failed for %s: %s", filename, exc)
            return {"ok": False, "error": f"Audio conversion failed: {exc}"}

        try:
            result = self._transcribe_wav_chunks(
                wav_chunks, word_timestamps=True, diarize=diarize, on_progress=progress
            )
        except Exception as exc:
            logger.error("Transcription failed for %s: %s", filename, exc)
            return {"ok": False, "error": f"Transcription failed: {exc}"}

        notes = list(result.notes)
        cleaned_text = None
        if cleanup_level and result.text:
            try:
                cleaned = clean_transcript(result.text, cleanup_level, self._config.llm, progress)
            except Exception as exc:  # clean_transcript reports model failures itself; this is a bug
                logger.exception("Cleaning up the transcript of %s failed: %s", filename, exc)
                notes.append("Text cleanup failed; the transcript is saved as transcribed.")
            else:
                if cleaned.note:
                    notes.append(cleaned.note)
                if cleaned.text != result.text:
                    cleaned_text = cleaned.text

        return self._store_file_transcription(
            filename,
            notes,
            progress,
            text=result.text,
            duration_ms=duration_ms,
            confidence=result.confidence,
            words=result.words,
            cleaned_text=cleaned_text,
            cleanup_level=cleanup_level,
            processing_ms=int((time.monotonic() - started) * 1000),
        )

    def _store_file_transcription(self, filename: str, notes: list[str], progress, **fields) -> dict:
        """Save a finished file transcription, run the extension hook and build the job result (blocking).

        `fields` go to `Database.save_file_transcription`; the language and model are the current settings.
        """
        if progress:
            progress("saving")
        try:
            record = self._db.save_file_transcription(
                language=self._config.language,
                original_filename=filename,
                model=self._config.inference.model,
                **fields,
            )
        except Exception as exc:
            logger.error("Saving the transcription of %s failed: %s", filename, exc)
            return {"ok": False, "error": f"Saving the transcript failed: {exc}"}

        extension_results: dict = {}
        try:
            extension_results, extension_notes = self.extensions.file_transcribed(file_transcript_from_record(record))
            notes.extend(extension_notes)
        except Exception as exc:  # the transcript is stored; extensions are an extra
            logger.exception("Extensions could not process the transcription of %s: %s", filename, exc)
            notes.append(f"Extensions could not process this transcript: {exc}")

        return {
            "ok": True,
            "notes": notes,
            "extensions": extension_results,
            "id": record.id,
            "kind": record.kind,
            "text": record.text,
            "cleaned_text": record.cleaned_text,
            "cleanup_level": record.cleanup_level,
            "language": record.language,
            "duration_ms": record.duration_ms,
            "model": record.model,
            "original_filename": record.original_filename,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

    # ---- Meetings -----------------------------------------------------------

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

    def set_meeting_consent_hint(self, enabled: bool) -> dict:
        self._config.meeting.consent_hint = bool(enabled)
        save_config(self._config)
        return self.get_settings()

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
        self._config.meeting.leave_dictation_out = bool(enabled)
        save_config(self._config)
        return self.get_settings()

    def set_meeting_live_transcript(self, enabled: bool) -> dict:
        """Applied from the next meeting on; the running one keeps what it started with."""
        self._config.meeting.live_transcript = bool(enabled)
        save_config(self._config)
        return self.get_settings()

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
        """The transcriber one live transcript uses for all its windows (current model and language)."""
        return create_transcriber(
            self._config.inference,
            device=None,
            language=self._config.language,
            auto_detect_language=self._config.auto_language_detection,
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

    def delete_file_transcription(self, tid: int) -> dict:
        """Delete an upload or a meeting with its audio: an upload's stored file, a meeting's whole folder
        (`mic.wav`, `system.wav`, `meeting.json`, `meeting.wav`). A file that is already gone does not stop it."""
        record = self._db.get_file_transcription(tid) if self._db else None
        if record is None:
            return {"ok": False, "error": "Unknown file transcription"}
        try:
            if (record.kind or "upload") == "meeting":
                self._remove_meeting_files(record)
            elif record.audio_path:
                path = (self._db_dir() / record.audio_path).resolve()
                if path.is_relative_to(self._db_dir()):
                    path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Deleting the audio of file transcription %s failed: %s", tid, exc)
            return {"ok": False, "error": f"Could not delete the audio files: {exc}"}
        self._db.delete_file_transcription(tid)
        return {"ok": True}

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

    def start_meeting_transcription(self, folder: str | Path) -> dict:
        """Transcribe a recorded meeting folder in the background (the same job registry uploads use)."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        folder = Path(folder)
        job = self._file_jobs.start(folder.name, lambda job: self.transcribe_meeting(folder, job=job))
        if job is None:
            return {"ok": False, "busy": True, "error": "Another file is still being transcribed"}
        return {"ok": True, "job_id": job.id}

    def transcribe_meeting(self, folder: str | Path, *, job: FileJob | None = None) -> dict:
        """Transcribe both tracks of a meeting folder, merge them into one transcript with numbered speakers, mix
        `meeting.wav` and save the result as a `kind="meeting"` row (blocking). No upload size limit applies.
        """
        folder = Path(folder)
        progress = job.update if job else None
        if progress:
            progress("converting")
        try:
            meeting = load_meeting(folder)
        except Exception as exc:
            logger.error("Loading meeting %s failed: %s", folder, exc)
            return {"ok": False, "error": f"Could not load the meeting: {exc}"}
        notes = list(meeting.notes)
        if not meeting.tracks:
            return {"ok": False, "error": "The meeting has no readable audio track"}

        words: dict[str, list] = {}
        plain: dict[str, str] = {}
        incomplete: list[str] = []
        confidences: list[float] = []
        tracks_16k: dict[str, Path] = {}
        levels: dict[str, list] = {}  # each track's loudness steps, for lining the tracks up
        chunk_s = MAX_CHUNK_MS // 1000
        total_chunks = sum(max(1, -(-t.frames // t.sample_rate // chunk_s)) for t in meeting.tracks.values())
        done_chunks = 0
        with tempfile.TemporaryDirectory(prefix="nexusvox-meeting-") as tmp:
            for name, track in meeting.tracks.items():
                label = TRACK_NAMES[name]
                try:
                    chunks = convert_track(track, Path(tmp) / f"{name}.wav", levels.setdefault(name, []))
                except Exception as exc:
                    logger.error("Audio conversion failed for meeting %s: %s", folder, exc)
                    return {"ok": False, "error": f"Audio conversion failed: {exc}"}
                tracks_16k[name] = Path(tmp) / f"{name}.wav"

                own_chunks = len(chunks)

                def track_progress(stage, current=0, total=0, base=done_chunks, own_chunks=own_chunks):
                    if progress:
                        # Speaker detection starts once the track's chunks are done: the bar stays where it was.
                        progress(stage, base + (own_chunks if stage == "diarizing" else current), total_chunks)

                # The system track holds everyone else, so it runs through speaker detection like an upload.
                # The whole track goes to the server in one request and is held a second time as one WAV, so a
                # very long meeting skips it (everyone on the system audio is then one speaker).
                detect_speakers = name == "system" and track.frames / track.sample_rate <= MAX_DETECTION_S
                if name == "system" and not detect_speakers:
                    notes.append(
                        f"{label}: The recording is too long for speaker detection; everyone on it is shown as "
                        f"{numbered_speaker_label(SPEAKER_FIRST_SYSTEM)}."
                    )

                try:
                    result = self._transcribe_wav_chunks(
                        chunks, word_timestamps=True, diarize=detect_speakers, on_progress=track_progress
                    )
                except Exception as exc:
                    logger.error("Transcription failed for meeting %s: %s", folder, exc)
                    return {"ok": False, "error": f"Transcription failed: {exc}"}
                done_chunks += len(chunks)
                del chunks  # one track's chunks at a time
                alone = f"everyone on it is shown as {numbered_speaker_label(SPEAKER_FIRST_SYSTEM)}."
                notes.extend(f"{label}: {n.replace(NO_SPEAKER_LABELS, alone)}" for n in result.notes)
                words[name] = list(result.words)
                detected = any(w.speaker is not None for w in result.words)
                if detect_speakers and not detected and not any(alone in n for n in notes):
                    notes.append(f"{label}: Speaker detection gave no speakers; {alone}")
                # Detection labels `text` "Speaker N: ..."; the meeting writes its own labels from the words.
                plain[name] = result.plain_text or result.text
                if result.confidence is not None:
                    confidences.append(result.confidence)
                # The server skips a word whose timing it cannot read; the labelled text is built from timed words.
                if len(result.words) < len(plain[name].split()):
                    incomplete.append(label)

            # The tracks are lined up (one of them delayed), in the words and in the mix alike.
            try:
                offset, measured = track_offset(meeting, levels.get("mic"), levels.get("system"))
            except Exception as exc:  # the transcription is done: never lose it over the alignment
                logger.exception("Lining up the tracks of meeting %s failed: %s", folder, exc)
                notes.append(f"The tracks could not be lined up from the audio ({exc}); the stream delays are used.")
                offset, measured = track_offset(meeting)
            mic_shift, system_shift = track_shifts(offset)
            logger.info(
                "Meeting %s: system track is %+.3f s late against the microphone (%s)", folder.name, offset, measured
            )

            if incomplete:
                # Interleaving needs every word timed: a gap would vanish from the merged text. Keep all the words.
                notes.append(
                    f"Word timing is missing or incomplete for {' and '.join(incomplete)}, so the meeting is "
                    "not interleaved by time: each track's text follows the other."
                )
                if any(w.speaker is not None for w in words.get("system", [])):
                    notes.append(
                        "The detected speakers could not be used without the word timing; everyone on the system "
                        f"audio is shown as {numbered_speaker_label(SPEAKER_FIRST_SYSTEM)}."
                    )
                own_label = self._config.meeting.own_voice_label
                text = "\n\n".join(
                    f"{label}: {plain[n]}"
                    for n, label in (("mic", own_label), ("system", numbered_speaker_label(SPEAKER_FIRST_SYSTEM)))
                    if plain.get(n)
                )
                merged = []
                if self._config.meeting.leave_dictation_out and meeting.dictations:
                    notes.append(
                        "Dictation could not be left out of the transcript because the word timing is missing."
                    )
            else:
                leave_out = self._config.meeting.leave_dictation_out
                merged = merge_words(
                    words.get("mic", []),
                    words.get("system", []),
                    meeting.dictations if leave_out else None,
                    system_offset=offset,
                )
                text = format_meeting(merged, self._config.meeting.own_voice_label)

            if progress:
                progress("saving")
            mix_path: Path | None = folder / MIX_FILENAME
            try:
                seconds = mix_tracks(
                    list(tracks_16k.values()),
                    mix_path,
                    [system_shift if n == "system" else mic_shift for n in tracks_16k],
                )
            except Exception as exc:
                logger.exception("Mixing meeting %s failed: %s", folder, exc)
                notes.append(f"The playable mix could not be written: {exc}")
                mix_path, seconds = None, max(t.frames / t.sample_rate for t in meeting.tracks.values())

        return self._store_file_transcription(
            folder.name,
            notes,
            progress,
            text=text,
            duration_ms=round(seconds * 1000),
            confidence=sum(confidences) / len(confidences) if confidences else None,
            words=merged,
            speaker_names={SPEAKER_OWN: self._config.meeting.own_voice_label},
            kind="meeting",
            audio_path=self._stored_audio_path(mix_path) if mix_path else None,
        )

    def _stored_audio_path(self, path: Path) -> str:
        """`path` relative to the database folder when it lies inside it (like recordings), else absolute."""
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(self._db_dir()))
        except ValueError:
            return str(resolved)

    def get_file_audio_path(self, tid: int) -> str | None:
        """Absolute path of the playable audio kept for a file transcription (a meeting's mix), if any."""
        record = self._db.get_file_transcription(tid) if self._db else None
        if record is None or not record.audio_path:
            return None
        path = self._db_dir() / record.audio_path
        return str(path) if path.is_file() else None

    def _db_dir(self) -> Path:
        """The folder of the database file, which recordings' relative `audio_path`s are relative to."""
        return Path(self._config.database.path).resolve().parent

    def _transcribe_wav_chunks(
        self,
        wav_chunks: list[bytes],
        *,
        word_timestamps: bool = False,
        diarize: bool = False,
        on_progress: Callable[..., None] | None = None,
    ):
        """Transcribe WAV chunks with the current model and language settings (blocking)."""
        return asyncio.run(
            transcribe_file(
                wav_chunks,
                self._config.inference,
                language=self._config.language,
                auto_detect_language=self._config.auto_language_detection,
                word_timestamps=word_timestamps,
                diarize=diarize,
                on_progress=on_progress,
            )
        )

    def get_file_transcriptions(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        """Return file transcription records; `kind` (`upload` / `meeting`) narrows them."""
        if self._db is None:
            return []
        rows = self._db.get_file_transcriptions(limit, kind)
        # One budget for the whole list, so a slow extension delays it by one timeout, not one per row.
        deadline = time.monotonic() + HOOK_TIMEOUT_S
        out = []
        for r in rows:
            text, cleaned_text = self._displayed(r, deadline)
            out.append(
                {
                    "id": r.id,
                    "text": text,
                    "cleaned_text": cleaned_text,
                    "cleanup_level": r.cleanup_level,
                    "language": r.language,
                    "duration_ms": r.duration_ms,
                    "model": r.model,
                    "original_filename": r.original_filename,
                    "kind": r.kind or "upload",
                    "has_audio": bool(r.audio_path),
                    "confidence": r.confidence,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "exports_unavailable": self._unavailable_exports(r, deadline),
                    "actions_unavailable": self.extensions.unavailable_actions(r.id, deadline=deadline),
                }
            )
        return out

    def _displayed(self, record, deadline: float) -> tuple[str, str | None]:
        """A row's `text` and `cleaned_text` as the extensions' `display_text` hook shows them, in one call."""
        if not self.extensions.extensions:  # nothing to ask: skip building the hook view (parses the word timings)
            return record.text, record.cleaned_text
        texts = [record.text, *([record.cleaned_text] if record.cleaned_text else [])]
        shown = self.extensions.display_texts(file_transcript_from_record(record), texts, deadline=deadline)
        return shown[0], shown[1] if len(shown) > 1 else record.cleaned_text

    def _unavailable_exports(self, record, deadline: float | None = None) -> dict[str, str | dict]:
        """`{target id: reason}` for the export targets that cannot export this row: the extensions' own checks,
        plus the built-in subtitle targets when no word timing is stored."""
        unavailable = self.extensions.unavailable_exports(record.id, deadline=deadline)
        if not record.words or record.words.strip() == "[]":
            for target_id in SUBTITLE_TARGET_IDS:
                if self.extensions.export_target(target_id) is not None:
                    unavailable.setdefault(target_id, NO_TIMING_HINT)
        return unavailable

    def export_file_transcription(self, transcription_id: int, target_id: str) -> dict:
        """Export a stored file transcription with an export target (built in or an extension's).

        `{"ok": True, "filename", "mimetype", "data": bytes}`; `status` says which HTTP error fits a failure.
        """
        target = self.extensions.export_target(target_id)
        if target is None:
            return {"ok": False, "status": 404, "error": "Unknown export target"}
        record = self._db.get_file_transcription(transcription_id) if self._db else None
        if record is None:
            return {"ok": False, "status": 404, "error": "Unknown file transcription"}
        try:
            data = target.export(file_transcript_from_record(record))
            if isinstance(data, str):
                data = data.encode("utf-8")
            if not isinstance(data, bytes):
                raise TypeError(f"expected bytes or str, got {type(data).__name__}")
        except Exception as exc:
            logger.exception("Export target %s failed on file transcription %s", target_id, transcription_id)
            return {"ok": False, "status": 500, "error": f"Export failed: {exc}"}
        stem = Path(record.original_filename).stem or "transcript"
        return {"ok": True, "filename": stem + target.extension, "mimetype": target.mimetype, "data": data}

    # ---- Benchmarks (Dev tab) ----------------------------------------------

    def get_benchmarks(self) -> list[dict]:
        """List all benchmark files with summary stats."""
        return bench.list_benchmarks(self._benchmarks_dir)

    def get_benchmark(self, filename: str) -> dict | None:
        """Load full benchmark data for a single file."""
        return bench.load_benchmark(self._benchmarks_dir, filename)

    def get_benchmark_comparison(self) -> dict:
        """Cross-model comparison with pass/fail against WER targets."""
        return bench.compare_benchmarks(self._benchmarks_dir)
