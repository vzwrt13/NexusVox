"""Dashboard API: voice commands and the experimental nexus OS commands."""

from __future__ import annotations

from ..config import save_config
from ..os_commands import NEXUS_ACTIONS
from ..voice_commands import ALL_STRUCTURAL_INFO, ALL_STRUCTURAL_KEYWORDS, ALL_SYMBOL_INFO
from .api_base import DashboardState


class CommandsMixin(DashboardState):
    """Voice command settings, the `[experimental]` switches and the OS commands."""

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

    @property
    def experimental(self):
        """The ``[experimental]`` switches: features that are hidden and unreachable in v1 unless turned on."""
        return self._config.experimental

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
