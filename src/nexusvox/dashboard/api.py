"""Dashboard API exposed to JavaScript via the pywebview JS bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from ..config import (
    CLEANUP_LEVELS,
    CUSTOM_PROTOCOLS,
    HOTKEY_KEY_VKS,
    HOTKEY_MODES,
    HOTKEY_MODIFIERS,
    MODEL_REGISTRY,
    SUPPORTED_LANGUAGES,
    Config,
    HotkeyConfig,
    normalize_hotkey_key,
    resolve_device,
    save_config,
    validate_hotkey,
)
from ..db import Database
from ..diarization import speaker_detection_models
from ..dictionary import suggest_entries
from ..extensions import ExtensionRegistry, file_transcript_from_record
from ..file_cleanup import clean_transcript
from ..file_transcribe import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, convert_to_wav, transcribe_file
from ..llm import is_reachable as llm_reachable
from ..llm import warm_up as llm_warm_up
from ..os_commands import NEXUS_ACTIONS
from ..polish_presets import PolishPreset
from ..snippets import Snippet, SnippetOption
from ..translator import is_reachable
from ..voice_commands import ALL_STRUCTURAL_INFO, ALL_STRUCTURAL_KEYWORDS, ALL_SYMBOL_INFO
from . import analytics
from . import benchmarks as bench
from .file_jobs import FileJob, FileJobs

logger = logging.getLogger(__name__)


# `[hotkey]` fields that hold a marker key, and how an error message names each.
_MARKER_KEY_FIELDS = {"paste_key": "paste", "polish_key": "polish"}


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
        benchmarks_dir: Path | None = None,
        extensions: ExtensionRegistry | None = None,
    ) -> None:
        self._sf = session_factory
        self._config = config
        self._db = db
        self._on_model_switch = on_model_switch
        self._get_switch_status = get_switch_status
        self._get_learn_status = get_learn_status
        self._benchmarks_dir = benchmarks_dir or Path("benchmarks")
        self._file_jobs = FileJobs()
        self.extensions = extensions if extensions is not None else ExtensionRegistry()

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
        # `diarization`: the upload tab's speaker detection works with this model; `diarization_models`
        # names the models it works with, for the hint when it does not.
        return {
            "model": model,
            "diarization": bool(MODEL_REGISTRY.get(model, {}).get("diarization")),
            "diarization_models": speaker_detection_models(),
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
            "store_audio": self._config.database.store_audio,
            "learn_from_edits": self._config.learn_from_edits.enabled,
            "polish_in_terminals": self._config.polish.in_terminals,
            "cleanup_level": self._config.cleanup.level,
            "cleanup_levels": list(CLEANUP_LEVELS),
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
            return {"counts": {}, "last": None}
        return self._get_learn_status()

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
        return analytics.get_overview(self._sf, start=start, end=end)

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
        from pathlib import Path

        from ..models import Transcription

        with self._sf() as session:
            row = session.get(Transcription, tid)
            if row and row.audio_path:
                db_dir = Path(self._config.database.path).resolve().parent
                return str(db_dir / row.audio_path)
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

        if progress:
            progress("saving")
        try:
            record = self._db.save_file_transcription(
                text=result.text,
                language=self._config.language,
                duration_ms=duration_ms,
                original_filename=filename,
                model=self._config.inference.model,
                confidence=result.confidence,
                words=result.words,
                cleaned_text=cleaned_text,
                cleanup_level=cleanup_level,
            )
        except Exception as exc:
            logger.error("Saving the transcription of %s failed: %s", filename, exc)
            return {"ok": False, "error": f"Saving the transcript failed: {exc}"}

        extension_results, extension_notes = self.extensions.file_transcribed(file_transcript_from_record(record))
        notes.extend(extension_notes)

        return {
            "ok": True,
            "notes": notes,
            "extensions": extension_results,
            "id": record.id,
            "text": record.text,
            "cleaned_text": record.cleaned_text,
            "cleanup_level": record.cleanup_level,
            "language": record.language,
            "duration_ms": record.duration_ms,
            "model": record.model,
            "original_filename": record.original_filename,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

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

    def get_file_transcriptions(self, limit: int = 50) -> list[dict]:
        """Return file transcription records."""
        if self._db is None:
            return []
        rows = self._db.get_file_transcriptions(limit)
        return [
            {
                "id": r.id,
                "text": r.text,
                "cleaned_text": r.cleaned_text,
                "cleanup_level": r.cleanup_level,
                "language": r.language,
                "duration_ms": r.duration_ms,
                "model": r.model,
                "original_filename": r.original_filename,
                "confidence": r.confidence,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    def export_file_transcription(self, transcription_id: int, target_id: str) -> dict:
        """Export a stored file transcription with an extension's export target.

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
