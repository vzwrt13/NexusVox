"""Nexus OS command parsing and dispatch for voice-triggered window management."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .config import OSCommandsConfig

logger = logging.getLogger(__name__)

NEXUS_ACTIONS: list[dict[str, str]] = [
    {"action": "open", "syntax": "nexus open <app>", "description": "Launch the application"},
    {"action": "close", "syntax": "nexus close <app>", "description": "Close the application window"},
    {"action": "focus", "syntax": "nexus focus <app>", "description": "Bring window to foreground"},
    {"action": "fullscreen", "syntax": "nexus fullscreen <app>", "description": "Toggle fullscreen (F11)"},
    {"action": "minimize", "syntax": "nexus minimize <app>", "description": "Minimize the window"},
    {"action": "snap left", "syntax": "nexus snap left <app>", "description": "Snap window to left half"},
    {"action": "snap right", "syntax": "nexus snap right <app>", "description": "Snap window to right half"},
    {"action": "flag", "syntax": "nexus flag", "description": "Flag the last transcription for review"},
]

_NEXUS_PATTERN = re.compile(
    r"^\s*nexus\s+(snap\s+(?:left|right)|open|close|focus|fullscreen|minimize)\s+(.+?)\s*$",
    re.IGNORECASE,
)

_NEXUS_FLAG_PATTERN = re.compile(
    r"^\s*nexus\s+flag\s*$",
    re.IGNORECASE,
)


@dataclass
class NexusCommand:
    action: str  # e.g. "open", "snap left", "flag"
    app_name: str | None  # e.g. "chrome", None for flag


def parse_nexus_command(text: str) -> NexusCommand | None:
    """Parse transcribed text into a NexusCommand, or None if not a nexus command.

    The regex matches the entire string so partial mentions like
    "I said nexus open chrome to my friend" do not trigger.
    """
    if _NEXUS_FLAG_PATTERN.match(text):
        return NexusCommand(action="flag", app_name=None)
    m = _NEXUS_PATTERN.match(text)
    if not m:
        return None
    action = " ".join(m.group(1).lower().split())
    app_name = m.group(2).lower().strip()
    return NexusCommand(action=action, app_name=app_name)


def execute_nexus_command(command: NexusCommand, config: OSCommandsConfig) -> bool:
    """Execute a window-management nexus command. Returns True if executed, False if app not found."""
    from . import window_manager

    if command.app_name is None:
        logger.warning("execute_nexus_command called with no app_name (action=%s)", command.action)
        return False

    exe_path = config.apps.get(command.app_name)
    if exe_path is None:
        logger.warning("Nexus command: unknown app '%s'", command.app_name)
        return False

    logger.info("Nexus command: %s %s (exe=%s)", command.action, command.app_name, exe_path)

    match command.action:
        case "open":
            window_manager.open_app(exe_path)
        case "close":
            window_manager.close_app(command.app_name, exe_path)
        case "focus":
            window_manager.focus_app(command.app_name, exe_path)
        case "fullscreen":
            window_manager.fullscreen_app(command.app_name, exe_path)
        case "minimize":
            window_manager.minimize_app(command.app_name, exe_path)
        case "snap left":
            window_manager.snap_app(command.app_name, "left", exe_path)
        case "snap right":
            window_manager.snap_app(command.app_name, "right", exe_path)

    return True
