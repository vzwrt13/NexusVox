"""Dashboard API: the settings page (hotkey, marker keys, language model, feedback, privacy)."""

from __future__ import annotations

import os
import secrets

from ..config import (
    CLEANUP_LEVELS,
    FEEDBACK_MODES,
    HOTKEY_KEY_VKS,
    HOTKEY_MODES,
    HOTKEY_MODIFIERS,
    HotkeyConfig,
    MeetingConfig,
    cuda_available,
    gpu_vram_gb,
    llm_runs_fast,
    meeting_hotkey_clash,
    normalize_hotkey_key,
    save_config,
    validate_hotkey,
)
from ..dictation.edit_watch import WATCHED_KEYS
from ..dictation.translator import is_reachable
from ..llm import build_model_list as llm_build_model_list
from ..llm import loaded_model as llm_loaded_model
from ..llm import model_pull as llm_pull
from ..llm import model_status as llm_model_status
from ..llm import warm_up as llm_warm_up
from ..system.feedback_form import build_feedback_url
from ..system.feedback_send import FeedbackError, app_feedback_url, build_report, render_preview, send_report
from ..system.logsetup import log_dir
from .api_base import DashboardState, logger

# `[hotkey]` fields that hold a marker key, and how an error message names each.
_MARKER_KEY_FIELDS = {"paste_key": "paste", "polish_key": "polish"}


class SettingsMixin(DashboardState):
    """`GET /api/settings` and the switches that write the shared config."""

    def get_settings(self) -> dict:
        return {
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
            "llm_slow_hint": not llm_runs_fast(),
            "meeting_modifiers": list(self._config.meeting.modifiers),
            "meeting_key": self._config.meeting.key,
            "meeting_hotkey_default": {"modifiers": list(MeetingConfig().modifiers), "key": MeetingConfig().key},
            "meeting_mic_device": self._config.meeting.mic_device,
            "meeting_output_device": self._config.meeting.output_device,
            "meeting_consent_hint": self._config.meeting.consent_hint,
            "meeting_leave_dictation_out": self._config.meeting.leave_dictation_out,
            "meeting_own_voice_label": self._config.meeting.own_voice_label,
            "meeting_live_transcript": self._config.meeting.live_transcript,
            "meeting_store_audio": self._config.meeting.store_audio,
            "feedback_url": build_feedback_url(
                self._config.support.feedback_form_url, device=self._config.inference.device
            ),
            "feedback_app_send": bool(app_feedback_url(self._config.support.feedback_form_url)),
        }

    def get_translator_status(self) -> dict:
        """Whether the external translator server answers - probed live, so the
        dashboard can say when the translate toggle would have no effect."""
        return {
            "url": self._config.translator.url,
            "reachable": is_reachable(self._config.translator),
        }

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
        if error is None and key:
            error = self._marker_clash(normalize_hotkey_key(key) or "")
        if error is not None:
            return {"error": error, **self.get_settings()}
        # Keep the canonical order so the config file and the hint read the same everywhere.
        self._config.hotkey.modifiers = [m for m in HOTKEY_MODIFIERS if m in modifiers]
        self._config.hotkey.key = normalize_hotkey_key(key) or ""
        save_config(self._config)
        return self.get_settings()

    def _marker_clash(self, name: str, field: str = "") -> str | None:
        """Why `name` cannot be taken: the listener would silently ignore or misread it, because the os-control
        command key wins a clash and the binding's own key is never a marker. `field` is the marker being set.
        """
        if name and self._config.os_control.enabled and name == self._config.os_control.command_key:
            return f"{name} is the os-control command key"
        for other, label in _MARKER_KEY_FIELDS.items():
            if name and other != field and name == getattr(self._config.hotkey, other):
                return f"{name} is already the {label} key"
        return None

    def _set_marker_key(self, field: str, key: str) -> dict:
        """Set one of `_MARKER_KEY_FIELDS`. Live: the listener reads the config on every key event."""
        name = normalize_hotkey_key(str(key or ""))
        if name is None:
            return {"error": f"Unknown key: {key}", **self.get_settings()}
        hotkey = self._config.hotkey
        if name and name == hotkey.key:
            return {"error": f"{name} already completes the hotkey", **self.get_settings()}
        clash = self._marker_clash(name, field)
        if clash:
            return {"error": clash, **self.get_settings()}
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
        """Whether the [llm] server answers and has the configured model - probed live, like the translator."""
        status = llm_model_status(self._config.llm)
        return {
            "url": self._config.llm.url,
            "protocol": self._config.llm.protocol,
            "model": self._config.llm.model,
            "reachable": status["state"] != "down",
            "model_installed": {"ok": True, "missing": False}.get(status["state"]),
        }

    def get_llm_models(self) -> dict:
        """The dropdown's content: recommended models, other installed ones, and the saved value."""
        cuda = cuda_available()
        models = llm_build_model_list(self._config.llm, cuda, gpu_vram_gb() if cuda else None)
        selected = self._config.llm.model
        known = [*models["recommended"], *models["other"]]
        disk_gb = next((m["size_gb"] for m in known if m["name"] == selected), None)
        return {
            **models,
            "selected": selected,
            "device": "gpu" if cuda else "cpu",
            "loaded": llm_loaded_model(self._config.llm, selected, disk_gb) if models["reachable"] else None,
            "slow_hint": not llm_runs_fast(),
        }

    def start_llm_pull(self, model: str) -> dict:
        """Download a model through Ollama. Only ever called after the user confirmed it in the dashboard."""
        model = str(model or "").strip()
        if not model:
            return {"error": "A model name is required"}
        if self._config.llm.protocol != "ollama":
            return {"error": "Downloading models needs an Ollama server"}
        if not llm_pull.start(self._config.llm, model):
            return {"error": "A download is already running", **llm_pull.state()}
        return llm_pull.state()

    def get_llm_pull(self) -> dict:
        return llm_pull.state()

    def cancel_llm_pull(self) -> dict:
        llm_pull.cancel()
        return llm_pull.state()

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

    def preview_feedback(self, kind: object, message: object, include_log: bool) -> dict:
        """What a report would send, as text; sends nothing. The reply carries a token for exactly this report."""
        try:
            url, report = self._feedback_report(kind, message, include_log)
        except FeedbackError as exc:
            return exc.as_dict()
        token = secrets.token_hex(8)
        self._feedback_preview = (token, (kind, message, include_log), url, report)
        return {"ok": True, "preview": render_preview(url, report), "token": token, "log_included": "log" in report}

    def send_feedback(self, kind: object, message: object, include_log: bool, token: object = None) -> dict:
        """Posts the report to the Worker. Only the Send click calls this; nothing is sent at any other time.

        With the token of a preview the user saw, that very report goes out, not a new one built from a log that
        has grown since. A token that no longer matches the inputs sends nothing (`stale`)."""
        try:
            if token is None:
                url, report = self._feedback_report(kind, message, include_log)
            else:
                held = self._feedback_preview
                if held is None or held[0] != token or held[1] != (kind, message, include_log):
                    raise FeedbackError("stale", "The report changed since the preview. Check it and click Send again.")
                _, _, url, report = held
            send_report(url, report)
        except FeedbackError as exc:
            return exc.as_dict()
        self._feedback_preview = None
        return {"ok": True, "log_included": "log" in report}

    def _feedback_report(self, kind: object, message: object, include_log: bool) -> tuple[str, dict[str, str]]:
        url = app_feedback_url(self._config.support.feedback_form_url)
        if not url:
            raise FeedbackError("disabled", "Sending from the app is not available here. Use the web form.")
        return url, build_report(kind, message, include_log, device=self._config.inference.device)

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
