"""Tests for the hotkey listener: hold and toggle modes, key bindings, swallowing.

Key events are fed by hand as Win32 virtual-key codes, exactly as the low-level hook
would deliver them. `_handle` returns True when the event must be swallowed.
"""

import pytest

# pynput picks a backend at import time and fails on a headless Linux runner
# (no X display), so these tests only run where the listener can be imported.
pytest.importorskip("pynput.keyboard", reason="pynput needs a display backend", exc_type=ImportError)

from nexusvox.config import HotkeyConfig, OsControlConfig  # noqa: E402
from nexusvox.hotkey import HotkeyListener, describe_hotkey  # noqa: E402

LCTRL, RCTRL, LSHIFT, LALT = 0xA2, 0xA3, 0xA0, 0xA4
VK_A, VK_B, VK_F9 = ord("A"), ord("B"), 0x78


def _listener(mode: str, modifiers=("ctrl", "alt"), key: str = ""):
    events: list[str] = []
    config = HotkeyConfig(modifiers=list(modifiers), key=key, mode=mode)
    hk = HotkeyListener(config, lambda: events.append("start"), lambda: events.append("stop"))
    return hk, events, config


def _press_combo(hk):
    hk._handle(LCTRL, True)
    hk._handle(LALT, True)


def _release_combo(hk):
    hk._handle(LALT, False)
    hk._handle(LCTRL, False)


# -- modifier-only bindings (unchanged behaviour) ---------------------------------


def test_hold_mode_records_while_held():
    hk, events, _ = _listener("hold")
    _press_combo(hk)
    assert events == ["start"] and hk.active
    hk._handle(LALT, False)  # first modifier up ends the hold
    assert events == ["start", "stop"] and not hk.active
    hk._handle(LCTRL, False)
    assert events == ["start", "stop"]


def test_hold_mode_ignores_partial_combo_and_other_keys():
    hk, events, _ = _listener("hold")
    hk._handle(LCTRL, True)
    assert hk._handle(VK_B, True) is False
    assert hk._handle(VK_B, False) is False
    hk._handle(LCTRL, False)
    assert events == []


def test_modifiers_are_never_swallowed_and_either_side_counts():
    hk, events, _ = _listener("hold")
    assert hk._handle(RCTRL, True) is False
    assert hk._handle(LALT, True) is False
    assert events == ["start"]
    assert hk._handle(RCTRL, False) is False
    assert events == ["start", "stop"]


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
    hk._handle(LALT, False)
    hk._handle(LALT, True)  # re-press one key while the other is still down: a new full press
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


# -- bindings that end in a key ---------------------------------------------------


def test_key_completes_the_combo_and_is_swallowed_in_hold_mode():
    hk, events, _ = _listener("hold", ("ctrl", "shift"), "A")
    assert hk._handle(LCTRL, True) is False
    assert hk._handle(LSHIFT, True) is False
    assert events == []  # modifiers alone do nothing now
    assert hk._handle(VK_A, True) is True
    assert events == ["start"] and hk.active
    assert hk._handle(VK_A, True) is True  # auto-repeat while held: still dead
    assert hk._handle(VK_A, False) is True  # its key-up too
    assert events == ["start", "stop"] and not hk.active
    assert hk._handle(LSHIFT, False) is False
    assert hk._handle(LCTRL, False) is False


def test_key_stays_dead_until_released_even_after_modifiers_go_up():
    hk, events, _ = _listener("hold", ("ctrl", "shift"), "A")
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    hk._handle(VK_A, True)
    hk._handle(LCTRL, False)  # hold ends here
    assert events == ["start", "stop"]
    assert hk._handle(VK_A, True) is True  # repeats of the still-held letter never type
    assert hk._handle(VK_A, False) is True
    assert hk._handle(VK_A, True) is False  # a fresh press types normally
    assert hk._handle(VK_A, False) is False
    assert events == ["start", "stop"]


def test_key_types_again_right_after_toggling():
    hk, events, _ = _listener("toggle", ("ctrl", "shift"), "A")
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    assert hk._handle(VK_A, True) is True
    assert hk._handle(VK_A, False) is True
    hk._handle(LSHIFT, False)
    hk._handle(LCTRL, False)
    assert events == ["start"] and hk.active
    # Recording runs; plain typing (including the bound letter) goes through.
    assert hk._handle(VK_A, True) is False
    assert hk._handle(VK_A, False) is False
    # Ctrl+Shift+A again stops.
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    assert hk._handle(VK_A, True) is True
    assert events == ["start", "stop"] and not hk.active


def test_key_without_all_modifiers_is_plain_typing():
    hk, events, _ = _listener("hold", ("ctrl", "shift"), "A")
    hk._handle(LCTRL, True)
    assert hk._handle(VK_A, True) is False  # Ctrl+A: select all, not ours
    assert hk._handle(VK_A, False) is False
    hk._handle(LCTRL, False)
    assert events == []


def test_key_must_come_last():
    hk, events, _ = _listener("hold", ("ctrl",), "A")
    hk._handle(VK_A, True)  # typing "a"...
    hk._handle(LCTRL, True)  # ...then pressing Ctrl must not start a recording
    assert events == []
    hk._handle(VK_A, False)
    hk._handle(LCTRL, False)
    assert events == []


def test_key_only_binding_needs_no_modifiers():
    hk, events, _ = _listener("hold", (), "F9")
    assert hk._handle(VK_F9, True) is True
    assert events == ["start"]
    assert hk._handle(VK_F9, False) is True
    assert events == ["start", "stop"]


def test_binding_change_is_live():
    hk, events, config = _listener("hold", ("ctrl", "alt"))
    _press_combo(hk)
    _release_combo(hk)
    assert events == ["start", "stop"]
    config.modifiers, config.key = ["ctrl"], "A"
    _press_combo(hk)  # old combo: Alt is no longer part of it, nothing happens
    assert events == ["start", "stop"]
    assert hk._handle(VK_A, True) is True
    assert events == ["start", "stop", "start"]


def test_describe_hotkey():
    assert describe_hotkey(HotkeyConfig(modifiers=["ctrl", "shift", "alt"])) == "Ctrl+Shift+Alt"
    assert describe_hotkey(HotkeyConfig(modifiers=["ctrl", "shift"], key="A")) == "Ctrl+Shift+A"
    assert describe_hotkey(HotkeyConfig(modifiers=[], key="PAGE_UP")) == "Page Up"


# -- command key (os-control) -----------------------------------------------------

VK_Y, VK_OEM_102, RALT = ord("Y"), 0xE2, 0xA5


def _command_listener(mode: str = "hold", enabled: bool = True, modifiers=("ctrl", "alt"), command_key: str = "Y"):
    events: list[str] = []
    config = HotkeyConfig(modifiers=list(modifiers), key="", mode=mode)
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        command_config=OsControlConfig(enabled=enabled, command_key=command_key),
        on_command=lambda: events.append("command"),
    )
    return hk, events


def test_command_key_during_hold_marks_the_recording_and_is_swallowed():
    hk, events = _command_listener()
    _press_combo(hk)
    assert hk._handle(VK_Y, True) is True
    assert hk._handle(VK_Y, True) is True  # auto-repeat
    assert hk._handle(VK_Y, False) is True
    _release_combo(hk)
    assert events == ["start", "command", "stop"]
    assert hk.command  # still readable after the stop


def test_command_key_fires_once_and_resets_on_next_recording():
    hk, events = _command_listener()
    _press_combo(hk)
    hk._handle(VK_Y, True)
    hk._handle(VK_Y, False)
    hk._handle(VK_Y, True)
    hk._handle(VK_Y, False)
    _release_combo(hk)
    assert events.count("command") == 1
    _press_combo(hk)
    assert not hk.command
    _release_combo(hk)


def test_command_key_before_the_last_modifier_types_normally():
    hk, events = _command_listener()
    hk._handle(LCTRL, True)
    assert hk._handle(VK_Y, True) is False
    hk._handle(LALT, True)
    assert hk._handle(VK_Y, False) is False
    _release_combo(hk)
    assert events == ["start", "stop"] and not hk.command


def test_command_key_with_some_binding_modifiers_types_normally():
    # Shift+Y (a capital Y) and AltGr+key (reported as LCtrl+RAlt) must reach the app
    # while the binding is Ctrl+Shift+Alt.
    hk, events = _command_listener(modifiers=("ctrl", "shift", "alt"))
    hk._handle(LSHIFT, True)
    assert hk._handle(VK_Y, True) is False
    assert hk._handle(VK_Y, False) is False
    hk._handle(LSHIFT, False)
    hk._handle(LCTRL, True)
    hk._handle(RALT, True)
    assert hk._handle(VK_Y, True) is False
    assert hk._handle(VK_Y, False) is False
    hk._handle(RALT, False)
    hk._handle(LCTRL, False)
    assert events == [] and not hk.command


def test_command_key_types_normally_outside_a_recording_or_when_disabled():
    hk, events = _command_listener()
    assert hk._handle(VK_Y, True) is False
    assert hk._handle(VK_Y, False) is False
    off, off_events = _command_listener(enabled=False)
    _press_combo(off)
    assert off._handle(VK_Y, True) is False
    _release_combo(off)
    assert "command" not in off_events and not off.command


def test_command_key_in_toggle_mode():
    hk, events = _command_listener(mode="toggle")
    _press_combo(hk)
    assert hk._handle(VK_Y, True) is True
    hk._handle(VK_Y, False)
    _release_combo(hk)
    _press_combo(hk)
    assert events == ["start", "command", "stop"] and hk.command


def test_bare_command_key_during_a_toggle_recording_types_normally():
    hk, events = _command_listener(mode="toggle")
    _press_combo(hk)
    _release_combo(hk)
    assert hk._handle(VK_Y, True) is False
    assert hk._handle(VK_Y, False) is False
    assert events == ["start"] and not hk.command


def test_oem_102_still_works_as_command_key():
    hk, events = _command_listener(command_key="OEM_102")
    _press_combo(hk)
    assert hk._handle(VK_OEM_102, True) is True
    assert hk._handle(VK_Y, True) is False  # Y is not the command key here
    hk._handle(VK_OEM_102, False)
    _release_combo(hk)
    assert events == ["start", "command", "stop"]
