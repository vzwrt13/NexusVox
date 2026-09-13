"""Tests for the hotkey listener's hold and toggle modes (key events fed by hand)."""

import pytest

# pynput picks a backend at import time and fails on a headless Linux runner
# (no X display), so these tests only run where the listener can be imported.
keyboard = pytest.importorskip("pynput.keyboard", reason="pynput needs a display backend", exc_type=ImportError)

from nexusvox.config import HotkeyConfig  # noqa: E402
from nexusvox.hotkey import HotkeyListener  # noqa: E402

CTRL, ALT = keyboard.Key.ctrl_l, keyboard.Key.alt_l


def _listener(mode: str):
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "alt"], mode=mode)
    hk = HotkeyListener(config, lambda: events.append("start"), lambda: events.append("stop"))
    return hk, events, config


def _press_combo(hk):
    hk._on_press(CTRL)
    hk._on_press(ALT)


def _release_combo(hk):
    hk._on_release(ALT)
    hk._on_release(CTRL)


def test_hold_mode_records_while_held():
    hk, events, _ = _listener("hold")
    _press_combo(hk)
    assert events == ["start"] and hk.active
    hk._on_release(ALT)  # first modifier up ends the hold
    assert events == ["start", "stop"] and not hk.active
    hk._on_release(CTRL)
    assert events == ["start", "stop"]


def test_hold_mode_ignores_partial_combo_and_other_keys():
    hk, events, _ = _listener("hold")
    hk._on_press(CTRL)
    hk._on_press(keyboard.KeyCode.from_char("c"))
    hk._on_release(keyboard.KeyCode.from_char("c"))
    hk._on_release(CTRL)
    assert events == []


def test_toggle_mode_starts_on_press_and_stops_on_next_press():
    hk, events, _ = _listener("toggle")
    _press_combo(hk)
    assert events == ["start"] and hk.active
    _release_combo(hk)
    assert events == ["start"] and hk.active  # letting go changes nothing
    _press_combo(hk)
    assert events == ["start", "stop"] and not hk.active
    _release_combo(hk)
    assert events == ["start", "stop"]


def test_toggle_mode_needs_a_fresh_press_per_flip():
    hk, events, _ = _listener("toggle")
    _press_combo(hk)
    hk._on_release(ALT)
    hk._on_press(ALT)  # re-press one key while the other is still down: a new full press
    assert events == ["start", "stop"]


def test_mode_switch_is_live():
    hk, events, config = _listener("hold")
    _press_combo(hk)
    _release_combo(hk)
    assert events == ["start", "stop"]
    config.mode = "toggle"
    _press_combo(hk)
    _release_combo(hk)
    assert events == ["start", "stop", "start"] and hk.active


def test_still_recording_uses_toggle_state_in_toggle_mode():
    hk, _, _ = _listener("toggle")
    _press_combo(hk)
    _release_combo(hk)
    assert hk.still_recording() is True
    _press_combo(hk)
    assert hk.still_recording() is False


def test_cancel_drops_intent_without_callback():
    hk, events, _ = _listener("toggle")
    _press_combo(hk)
    _release_combo(hk)
    hk.cancel()
    assert events == ["start"] and not hk.active and hk.still_recording() is False
    _press_combo(hk)  # the next press starts a new recording instead of "stopping" the lost one
    assert events == ["start", "start"] and hk.active
