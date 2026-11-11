"""Tests for nexus OS command parsing and dispatch."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from conftest import WINDOW_ACTIONS

from nexusvox.config import OSCommandsConfig
from nexusvox.dictation.os_commands import (
    NexusCommand,
    execute_nexus_command,
    parse_nexus_command,
)

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
    """Replace window_manager where `from ..system import window_manager` finds it, the package attribute.

    On Windows the attribute exists (`_tests_never_touch_real_windows` imports the module), so a stale target raises
    here. The real module cannot be imported elsewhere, so the attribute is created there.
    """
    import nexusvox.system

    mock = MagicMock()
    with patch.object(nexusvox.system, "window_manager", mock, create=sys.platform != "win32"):
        yield mock


@pytest.mark.skipif(sys.platform != "win32", reason="window_manager binds Win32 at import")
def test_the_guard_stops_every_real_window_action():
    from nexusvox.system import window_manager

    # A name no window has: should the guard ever fail, these calls find nothing to act on.
    for action in WINDOW_ACTIONS:
        with pytest.raises(AssertionError, match="real window action"):
            getattr(window_manager, action)("nexusvox-no-such-app", "nexusvox-no-such-app.exe")


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


# --- Parsing: punctuation, a comma after "nexus", German verbs ---


@pytest.mark.parametrize(
    "text",
    ["Nexus open Chrome.", "Nexus, open Chrome", "nexus, open chrome!", "Nexus open, Chrome.", "nexus open chrome?"],
)
def test_parse_survives_the_models_punctuation(text):
    assert parse_nexus_command(text) == NexusCommand(action="open", app_name="chrome")


@pytest.mark.parametrize("text", ["Nexus flag.", "Nexus, flag", "nexus flag!", "Nexus, markiere."])
def test_parse_flag_survives_punctuation(text):
    assert parse_nexus_command(text) == NexusCommand(action="flag", app_name=None)


@pytest.mark.parametrize(
    ("text", "action", "app"),
    [
        ("Nexus öffne Chrome", "open", "chrome"),
        ("Nexus, starte Spotify.", "open", "spotify"),
        ("Nexus schließe Spotify", "close", "spotify"),
        ("Nexus schliesse Spotify.", "close", "spotify"),
        ("Nexus fokussiere Terminal", "focus", "terminal"),
        ("Nexus Vollbild Chrome", "fullscreen", "chrome"),
        ("Nexus minimiere Discord", "minimize", "discord"),
        ("Nexus snap links Terminal", "snap left", "terminal"),
        ("Nexus, docke rechts Code.", "snap right", "code"),
        ("Nexus snap left terminal.", "snap left", "terminal"),
    ],
)
def test_parse_german_verbs(text, action, app):
    assert parse_nexus_command(text) == NexusCommand(action=action, app_name=app)


@pytest.mark.parametrize("text", ["Nexus markieren", "Nexus markiere", "nexus markieren."])
def test_parse_german_flag(text):
    assert parse_nexus_command(text) == NexusCommand(action="flag", app_name=None)


def test_parse_german_mid_sentence_returns_none():
    assert parse_nexus_command("Ich sagte, nexus öffne Chrome") is None
