"""Window manager: which windows an action may touch, and what close and open never do. User32 is mocked."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

if sys.platform != "win32":
    pytest.skip("window_manager binds Win32 at import", allow_module_level=True)

from nexusvox.system import window_manager as wm

# The autouse guard in conftest replaces the public actions; the tests need the real ones.
REAL_CLOSE = wm.close_app
REAL_OPEN = wm.open_app


def _windows(monkeypatch, windows: dict[int, tuple[str, str]]):
    """Enumerate *windows* {hwnd: (exe path, title)}; all visible."""
    user32 = MagicMock()
    user32.IsWindowVisible.return_value = True

    def enum(callback, _lparam):
        for hwnd in windows:
            if not callback(hwnd, 0):
                break

    user32.EnumWindows.side_effect = lambda cb, lp: enum(cb, lp)
    monkeypatch.setattr(wm, "user32", user32)
    monkeypatch.setattr(wm, "WNDENUMPROC", lambda f: f)
    monkeypatch.setattr(wm, "_window_exe", lambda hwnd: windows[hwnd][0])
    monkeypatch.setattr(wm, "_window_title", lambda hwnd: windows[hwnd][1])
    return user32


def test_find_window_ignores_a_title_that_only_mentions_the_app(monkeypatch):
    _windows(monkeypatch, {1: (r"C:\Chrome\chrome.exe", "Claude Code - Google Chrome")})
    assert wm.find_window("code", r"C:\VS\Code.exe") is None


def test_find_window_matches_the_apps_process(monkeypatch):
    _windows(
        monkeypatch,
        {1: (r"C:\Chrome\chrome.exe", "Claude Code"), 2: (r"C:\Microsoft VS Code\Code.exe", "main.py")},
    )
    assert wm.find_window("code", r"C:\Microsoft VS Code\Code.exe") == 2


def test_close_never_kills_an_app_that_is_still_prompting(monkeypatch):
    user32 = _windows(monkeypatch, {1: (r"C:\Office\WINWORD.EXE", "Save changes?")})
    user32.IsWindow.return_value = True  # still there after WM_CLOSE: asking to save
    user32.IsHungAppWindow.return_value = False
    run = MagicMock()
    monkeypatch.setattr(wm.subprocess, "run", run)
    monkeypatch.setattr(wm.time, "sleep", lambda s: None)
    REAL_CLOSE("word", r"C:\Office\WINWORD.EXE")
    user32.PostMessageW.assert_called_once()
    run.assert_not_called()


def test_close_kills_a_hung_window(monkeypatch):
    user32 = _windows(monkeypatch, {1: (r"C:\Office\WINWORD.EXE", "Word")})
    user32.IsWindow.return_value = True
    user32.IsHungAppWindow.return_value = True
    run = MagicMock()
    monkeypatch.setattr(wm.subprocess, "run", run)
    monkeypatch.setattr(wm.time, "sleep", lambda s: None)
    REAL_CLOSE("word", r"C:\Office\WINWORD.EXE")
    run.assert_called_once()


def test_find_window_needs_the_whole_process_name(monkeypatch):
    _windows(monkeypatch, {1: (r"C:\Teams\msedgewebview2.exe", "Teams"), 2: (r"C:\Office\wordpad.exe", "Doc")})
    assert wm.find_window("edge", r"C:\Edge\msedge.exe") is None
    assert wm.find_window("word", r"C:\Office\WINWORD.EXE") is None


def test_find_window_finds_a_uwp_window_by_title(monkeypatch):
    _windows(monkeypatch, {1: (r"C:\Windows\System32\ApplicationFrameHost.exe", "Calculator")})
    assert wm.find_window("calculator", "calc") == 1


def test_find_window_finds_a_launcher_apps_window_by_title(monkeypatch):
    _windows(monkeypatch, {1: (r"C:\Users\x\Discord\app-1\Discord.exe", "General - Discord")})
    assert wm.find_window("discord", r"C:\Users\x\Discord\Update.exe --processStart Discord.exe") == 1


def test_close_reports_that_no_window_was_found(monkeypatch):
    _windows(monkeypatch, {})
    assert REAL_CLOSE("word", r"C:\Office\WINWORD.EXE") is False


def test_close_reports_a_prompting_window_as_not_closed(monkeypatch):
    user32 = _windows(monkeypatch, {1: (r"C:\Office\WINWORD.EXE", "Save changes?")})
    user32.IsHungAppWindow.return_value = False
    monkeypatch.setattr(wm.time, "sleep", lambda s: None)
    monkeypatch.setattr(wm.subprocess, "run", MagicMock())
    assert REAL_CLOSE("word", r"C:\Office\WINWORD.EXE") is False


def test_close_counts_a_window_hidden_to_the_tray_as_closed(monkeypatch):
    user32 = _windows(monkeypatch, {1: (r"C:\Discord\Discord.exe", "Discord")})
    run = MagicMock()
    monkeypatch.setattr(wm.subprocess, "run", run)
    monkeypatch.setattr(wm.time, "sleep", lambda s: None)
    visible = iter([True, False])  # enumeration sees it, after WM_CLOSE it is hidden
    user32.IsWindowVisible.side_effect = lambda hwnd: next(visible)
    assert REAL_CLOSE("discord", r"C:\Discord\Discord.exe") is True
    run.assert_not_called()


def test_open_starts_an_existing_exe_without_the_shell(monkeypatch):
    path = r"C:\Program Files (x86)\App\app.exe"
    popen = MagicMock()
    monkeypatch.setattr(wm.subprocess, "Popen", popen)
    monkeypatch.setattr(wm.os.path, "isfile", lambda p: p == path)
    assert REAL_OPEN(f'"{path}"') is True  # quoted, as config.toml usually has it
    assert popen.call_args.args[0] == [path]
    assert "shell" not in popen.call_args.kwargs


def test_open_hands_a_shortcut_to_its_file_association(monkeypatch):
    path = r"C:\Start Menu\Spotify.lnk"
    popen, startfile = MagicMock(), MagicMock()
    monkeypatch.setattr(wm.subprocess, "Popen", popen)
    monkeypatch.setattr(wm.os, "startfile", startfile, raising=False)
    monkeypatch.setattr(wm.os.path, "isfile", lambda p: p == path)
    REAL_OPEN(path)
    startfile.assert_called_once_with(path)
    popen.assert_not_called()


def test_open_keeps_the_shell_for_a_command_that_is_not_a_file(monkeypatch):
    popen = MagicMock()
    monkeypatch.setattr(wm.subprocess, "Popen", popen)
    monkeypatch.setattr(wm.os.path, "isfile", lambda p: False)
    REAL_OPEN("calc")
    assert popen.call_args.args[0] == "calc"
    assert popen.call_args.kwargs["shell"] is True
