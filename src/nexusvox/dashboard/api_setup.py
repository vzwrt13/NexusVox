"""Dashboard API: the first-run setup (microphone choice and level, finishing or skipping)."""

from __future__ import annotations

from ..audio.devices import input_device_names
from ..config import save_config
from .api_base import DashboardState

MAX_DEVICE_NAME_CHARS = 200


class SetupMixin(DashboardState):
    """The endpoints behind the Welcome, Microphone, Hotkey and Ready steps; the hotkey, its mode and the feedback
    use the Settings endpoints."""

    def get_setup_state(self) -> dict:
        """Whether the first-run setup is still to be shown (a fresh install, until it is finished or skipped)."""
        return {"needed": not self._config.setup_done}

    def get_microphones(self) -> dict:
        """The input devices to choose from, plus the chosen one (empty: the system default input)."""
        return {"inputs": input_device_names(), "device": self._config.audio.device}

    def set_microphone(self, device: str) -> dict:
        """Used from the next recording on (the capture reads it at every start)."""
        device = str(device or "")
        if len(device) > MAX_DEVICE_NAME_CHARS:
            return {"error": "That device name is too long", **self.get_settings()}
        self._config.audio.device = device
        save_config(self._config)
        return self.get_settings()

    def read_mic_level(self) -> dict:
        """The level of the chosen microphone since the last read; opens the stream on the first read."""
        return self._level_meter.read(self._config.audio.device)

    def stop_mic_level(self) -> dict:
        self._level_meter.close()
        return {"ok": True}

    def complete_setup(self) -> dict:
        """Finished or skipped: the dashboard opens on Home from now on. Settings can run the setup again."""
        self._level_meter.close()
        self._config.setup_done = True
        save_config(self._config)
        return self.get_settings()
