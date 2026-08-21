"""Tests for nexus OS command parsing and dispatch."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from nexusvox.config import OSCommandsConfig
from nexusvox.os_commands import NexusCommand, execute_nexus_command, parse_nexus_command

# --- Parsing: valid commands ---


def test_parse_open():
    cmd = parse_nexus_command("nexus open chrome")
    assert cmd == NexusCommand(action="open", app_name="chrome")


def test_parse_close():
    cmd = parse_nexus_command("nexus close discord")
    assert cmd == NexusCommand(action="close", app_name="discord")


def test_parse_focus():
    cmd = parse_nexus_command("nexus focus terminal")
    assert cmd == NexusCommand(action="focus", app_name="terminal")


def test_parse_fullscreen():
    cmd = parse_nexus_command("nexus fullscreen chrome")
    assert cmd == NexusCommand(action="fullscreen", app_name="chrome")


def test_parse_minimize():
    cmd = parse_nexus_command("nexus minimize discord")
    assert cmd == NexusCommand(action="minimize", app_name="discord")


def test_parse_snap_left():
    cmd = parse_nexus_command("nexus snap left terminal")
    assert cmd == NexusCommand(action="snap left", app_name="terminal")


def test_parse_snap_right():
    cmd = parse_nexus_command("nexus snap right code")
    assert cmd == NexusCommand(action="snap right", app_name="code")


def test_parse_case_insensitive():
    cmd = parse_nexus_command("NEXUS OPEN CHROME")
    assert cmd == NexusCommand(action="open", app_name="chrome")


def test_parse_mixed_case():
    cmd = parse_nexus_command("Nexus Focus Discord")
    assert cmd == NexusCommand(action="focus", app_name="discord")


def test_parse_leading_trailing_whitespace():
    cmd = parse_nexus_command("  nexus open chrome  ")
    assert cmd == NexusCommand(action="open", app_name="chrome")


def test_parse_multi_word_app():
    cmd = parse_nexus_command("nexus open visual studio")
    assert cmd == NexusCommand(action="open", app_name="visual studio")


# --- Parsing: non-matching inputs ---


def test_parse_not_nexus_returns_none():
    assert parse_nexus_command("hello world") is None


def test_parse_partial_match_returns_none():
    assert parse_nexus_command("I said nexus open chrome yesterday") is None


def test_parse_unknown_action_returns_none():
    assert parse_nexus_command("nexus destroy chrome") is None


def test_parse_missing_app_returns_none():
    assert parse_nexus_command("nexus open") is None


def test_parse_nexus_only_returns_none():
    assert parse_nexus_command("nexus") is None


def test_parse_flag():
    cmd = parse_nexus_command("nexus flag")
    assert cmd == NexusCommand(action="flag", app_name=None)


def test_parse_flag_case_insensitive():
    cmd = parse_nexus_command("NEXUS FLAG")
    assert cmd == NexusCommand(action="flag", app_name=None)


def test_parse_flag_with_whitespace():
    cmd = parse_nexus_command("  nexus flag  ")
    assert cmd == NexusCommand(action="flag", app_name=None)


def test_parse_flag_with_extra_text_returns_none():
    assert parse_nexus_command("nexus flag something") is None


def test_parse_empty_string_returns_none():
    assert parse_nexus_command("") is None


# --- Dispatch ---


@pytest.fixture
def mock_wm():
    """Inject a mock window_manager into sys.modules so the lazy import works on all platforms."""
    mock = MagicMock()
    with patch.dict(sys.modules, {"nexusvox.window_manager": mock}):
        yield mock


def test_execute_open(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"chrome": "chrome.exe"})
    cmd = NexusCommand(action="open", app_name="chrome")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.open_app.assert_called_once_with("chrome.exe")


def test_execute_close(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"chrome": "chrome.exe"})
    cmd = NexusCommand(action="close", app_name="chrome")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.close_app.assert_called_once_with("chrome", "chrome.exe")


def test_execute_focus(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"discord": "discord.exe"})
    cmd = NexusCommand(action="focus", app_name="discord")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.focus_app.assert_called_once_with("discord", "discord.exe")


def test_execute_fullscreen(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"chrome": "chrome.exe"})
    cmd = NexusCommand(action="fullscreen", app_name="chrome")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.fullscreen_app.assert_called_once_with("chrome", "chrome.exe")


def test_execute_minimize(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"chrome": "chrome.exe"})
    cmd = NexusCommand(action="minimize", app_name="chrome")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.minimize_app.assert_called_once_with("chrome", "chrome.exe")


def test_execute_snap_left(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"terminal": "wt.exe"})
    cmd = NexusCommand(action="snap left", app_name="terminal")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.snap_app.assert_called_once_with("terminal", "left", "wt.exe")


def test_execute_snap_right(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"code": "code"})
    cmd = NexusCommand(action="snap right", app_name="code")
    assert execute_nexus_command(cmd, config) is True
    mock_wm.snap_app.assert_called_once_with("code", "right", "code")


def test_execute_no_app_name_returns_false(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"chrome": "chrome.exe"})
    cmd = NexusCommand(action="flag", app_name=None)
    assert execute_nexus_command(cmd, config) is False
    mock_wm.open_app.assert_not_called()


def test_execute_unknown_app_returns_false(mock_wm):
    config = OSCommandsConfig(enabled=True, apps={"chrome": "chrome.exe"})
    cmd = NexusCommand(action="open", app_name="unknown")
    assert execute_nexus_command(cmd, config) is False
    mock_wm.open_app.assert_not_called()
