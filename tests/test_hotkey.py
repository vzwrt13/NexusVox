"""Tests for the hotkey listener: hold and toggle modes, key bindings, swallowing.

Key events are fed by hand as Win32 virtual-key codes, exactly as the low-level hook
would deliver them. `_handle` returns True when the event must be swallowed.
"""

import pytest

# pynput picks a backend at import time and fails on a headless Linux runner
# (no X display), so these tests only run where the listener can be imported.
pytest.importorskip("pynput.keyboard", reason="pynput needs a display backend", exc_type=ImportError)

from nexusvox.config import HotkeyConfig, OsControlConfig  # noqa: E402
from nexusvox.hotkey import FOREIGN_KEY_WINDOW_S, PASTE_LAST, POLISH, HotkeyListener, describe_hotkey  # noqa: E402

LCTRL, RCTRL, LSHIFT, LALT = 0xA2, 0xA3, 0xA0, 0xA4
VK_A, VK_B, VK_F9 = ord("A"), ord("B"), 0x78


def _listener(mode: str, modifiers=("ctrl", "alt"), key: str = ""):
    events: list[str] = []
    config = HotkeyConfig(modifiers=list(modifiers), key=key, mode=mode)
    # grace_s=0: a Toggle stop chord finishes at once (the grace window has its own tests below)
    hk = HotkeyListener(
        config, lambda: events.append("start"), lambda: events.append("stop"), grace_s=0, physical_modifiers=set
    )
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


class FakeTimer:
    """Stands in for threading.Timer: `fire_all()` plays the elapsed grace window."""

    pending: list["FakeTimer"] = []

    def __init__(self, interval, fn):
        self.fn, self.daemon, self.cancelled = fn, False, False

    def start(self):
        FakeTimer.pending.append(self)

    def cancel(self):
        self.cancelled = True

    @classmethod
    def fire_all(cls):
        timers, cls.pending = cls.pending, []
        for t in timers:
            if not t.cancelled:
                t.fn()


def _command_listener(
    mode: str = "hold",
    enabled: bool = True,
    modifiers=("ctrl", "alt"),
    command_key: str = "Y",
    grace_s: float = 0,
):
    events: list[str] = []
    config = HotkeyConfig(modifiers=list(modifiers), key="", mode=mode)
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        command_config=OsControlConfig(enabled=enabled, command_key=command_key),
        on_command=lambda: events.append("command"),
        on_cancel=lambda: events.append("cancel"),
        grace_s=grace_s,
        timer_factory=FakeTimer,
        physical_modifiers=set,
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
    assert hk._handle(VK_Y, True) is False  # Y is not the command key here: it types, and discards the hold
    hk._handle(VK_OEM_102, False)
    _release_combo(hk)
    assert events == ["start", "command", "cancel"]


# -- paste key ---------------------------------------------------------------------

VK_V = ord("V")


def test_paste_key_during_hold_marks_paste_last_and_is_swallowed():
    hk, events = _command_listener()
    _press_combo(hk)
    assert hk._handle(VK_V, True) is True
    assert hk._handle(VK_V, True) is True  # auto-repeat
    assert hk._handle(VK_V, False) is True
    _release_combo(hk)
    assert events == ["start", "stop"]
    assert hk.mark == PASTE_LAST


def test_paste_key_types_normally_outside_a_recording_and_resets_next_time():
    hk, _ = _command_listener()
    assert hk._handle(VK_V, True) is False
    assert hk._handle(VK_V, False) is False
    _press_combo(hk)
    hk._handle(VK_V, True)
    hk._handle(VK_V, False)
    _release_combo(hk)
    _press_combo(hk)
    assert hk.mark is None
    _release_combo(hk)


def test_first_marker_wins():
    hk, events = _command_listener()
    _press_combo(hk)
    hk._handle(VK_V, True)
    hk._handle(VK_Y, True)
    _release_combo(hk)
    assert hk.mark == PASTE_LAST and "command" not in events


def test_paste_key_off_or_used_by_the_binding_types_normally():
    hk, _, config = _listener("hold")
    config.paste_key = ""
    _press_combo(hk)
    assert hk._handle(VK_V, True) is False
    _release_combo(hk)
    own, own_events, _ = _listener("hold", key="V")
    own._handle(LCTRL, True)
    own._handle(LALT, True)
    assert own._handle(VK_V, True) is True  # completes the combo, not a marker
    assert own_events == ["start"] and own.mark is None


# -- polish key --------------------------------------------------------------------

VK_P = ord("P")


def test_polish_key_marks_polish_and_paste_key_still_works():
    hk, events, config = _listener("hold")
    config.polish_key = "P"
    _press_combo(hk)
    assert hk._handle(VK_P, True) is True
    hk._handle(VK_P, False)
    _release_combo(hk)
    assert hk.mark == POLISH and events == ["start", "stop"]
    _press_combo(hk)
    assert hk._handle(ord("V"), True) is True
    _release_combo(hk)
    assert hk.mark == PASTE_LAST


def test_polish_key_off_by_default_types_normally():
    hk, _, _ = _listener("hold")
    _press_combo(hk)
    assert hk._handle(VK_P, True) is False
    _release_combo(hk)
    assert hk.mark is None


# -- Alt/Win menu mask -----------------------------------------------------------
#
# The mask must reach the app while Alt/Win is certainly down, i.e. when the combo completes and
# never at the release: sent from inside the hook it would queue behind the key-up it must precede.


def _masked(modifiers, key: str = ""):
    hk, events, _ = _listener("hold", modifiers=modifiers, key=key)
    log: list[str] = []
    hk._mask_sender = lambda: log.append("mask")
    return hk, log


RWIN, LALT_VK, RALT = 0x5C, LALT, 0xA5


@pytest.mark.parametrize("release_order", [(LCTRL, LSHIFT, LALT), (LALT, LSHIFT, LCTRL), (LSHIFT, LALT, LCTRL)])
def test_mask_is_sent_once_at_completion_not_at_release(release_order):
    hk, log = _masked(("ctrl", "shift", "alt"))
    for vk in (LCTRL, LSHIFT):
        hk._handle(vk, True)
    assert log == []
    hk._handle(LALT, True)
    assert log == ["mask"]
    for vk in release_order:
        hk._handle(vk, False)
    assert log == ["mask"]


def test_no_mask_without_alt_or_win_in_binding():
    hk, log = _masked(("ctrl", "shift"))
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    hk._handle(LSHIFT, False)
    hk._handle(LCTRL, False)
    assert log == []


def test_win_binding_is_masked_too():
    hk, log = _masked(("ctrl", "win"))
    hk._handle(LCTRL, True)
    hk._handle(0x5B, True)
    assert log == ["mask"]


def test_lone_alt_tap_is_not_masked():
    hk, log = _masked(("ctrl", "shift", "alt"))
    hk._handle(LALT, True)
    hk._handle(LALT, False)
    assert log == []


def test_key_binding_with_alt_masks_when_the_key_completes_it():
    hk, log = _masked(("ctrl", "alt"), key="A")
    _press_combo(hk)
    assert log == []
    hk._handle(VK_A, True)
    assert log == ["mask"]


def test_second_alt_pressed_during_the_combo_is_masked_again():
    hk, log = _masked(("ctrl", "alt"))
    _press_combo(hk)
    hk._handle(RALT, True)
    assert log == ["mask", "mask"]
    hk._handle(LALT, False)
    hk._handle(RALT, False)
    assert log == ["mask", "mask"]


def test_failing_mask_sender_does_not_break_the_hook():
    hk, events, _ = _listener("hold")

    def boom():
        raise OSError("blocked")

    hk._mask_sender = boom
    _press_combo(hk)
    assert events == ["start"]


def test_no_sender_means_no_injection_in_unit_tests():
    hk, events, _ = _listener("hold")
    assert hk._mask_sender is None
    _press_combo(hk)
    assert events == ["start"]


def test_auto_repeat_of_a_held_modifier_sends_no_extra_mask():
    hk, _, _ = _listener("hold", modifiers=("ctrl", "shift", "alt"))
    log: list[str] = []
    hk._mask_sender = lambda: log.append("mask")
    for vk in (LCTRL, LSHIFT, LALT):
        hk._handle(vk, True)
    assert log == ["mask"]  # the combo completing
    for _ in range(5):
        hk._handle(LALT, True)  # Windows repeats the key-down of a held key
    assert log == ["mask"]
    hk._handle(LALT, False)
    hk._handle(LALT, True)  # a real second press does get one
    assert log == ["mask", "mask"]


def test_right_win_counts_as_a_held_win_modifier(monkeypatch):
    import ctypes

    from nexusvox import hotkey

    class FakeUser32:
        @staticmethod
        def GetAsyncKeyState(vk):  # noqa: N802 - Win32 name
            return 0x8000 if vk in (0xA2, 0x5C) else 0  # Left Ctrl, Right Win

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": FakeUser32})(), raising=False)
    assert hotkey._physical_modifiers() == {"ctrl", "win"}
    hk, _, _ = _listener("hold", modifiers=("ctrl", "win"))
    assert hk.modifiers_physically_down() is True


# -- Toggle: marker keys end the recording, grace window, Escape -------------------

VK_P, VK_ESC = ord("P"), 0x1B


def _toggle(key: str = "", grace_s: float = 0.3, physical=None):
    FakeTimer.pending = []
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "alt"], key=key, mode="toggle", polish_key="P")
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        command_config=OsControlConfig(enabled=True, command_key="Y"),
        on_command=lambda: events.append("command"),
        on_cancel=lambda: events.append("cancel"),
        grace_s=grace_s,
        timer_factory=FakeTimer,
        physical_modifiers=lambda: set(physical or ()),
    )
    return hk, events


def test_toggle_modifier_only_plain_stop_waits_for_the_grace_window():
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    _press_combo(hk)
    assert events == ["start"] and hk.active  # not finished yet
    FakeTimer.fire_all()
    assert events == ["start", "stop"] and not hk.active and hk.mark is None


def test_toggle_modifier_only_marker_in_the_grace_window_stops_and_polishes():
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    _press_combo(hk)
    assert hk._handle(VK_P, True) is True
    assert events == ["start", "stop"] and hk.mark == POLISH
    FakeTimer.fire_all()  # the cancelled timer must not stop a second time
    assert events == ["start", "stop"]


@pytest.mark.parametrize(("vk", "mark"), [(VK_V, PASTE_LAST), (VK_Y, "command")])
def test_toggle_paste_and_command_markers_also_end_the_recording(vk, mark):
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    _press_combo(hk)
    assert hk._handle(vk, True) is True
    assert events[-1] == "stop" and hk.mark == mark


def test_toggle_key_binding_marker_stops_without_a_grace_window():
    hk, events = _toggle(key="A")
    hk._handle(LCTRL, True)
    hk._handle(LALT, True)
    hk._handle(VK_A, True)  # start
    hk._handle(VK_A, False)
    _release_combo(hk)
    hk._handle(LCTRL, True)
    hk._handle(LALT, True)  # modifiers back down, no chord press
    assert hk._handle(VK_P, True) is True
    assert events == ["start", "stop"] and hk.mark == POLISH


def test_toggle_marker_held_at_start_counts_and_plain_hotkey_ends_it():
    hk, events = _toggle()
    _press_combo(hk)
    assert hk._handle(VK_P, True) is True  # same hold that started it: marks only
    assert events == ["start"] and hk.active and hk.mark == POLISH
    hk._handle(VK_P, False)
    _release_combo(hk)
    _press_combo(hk)  # plain stop chord: the action chosen at the start stays
    FakeTimer.fire_all()
    assert events == ["start", "stop"] and hk.mark == POLISH


def test_toggle_marker_without_the_modifiers_types_normally():
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    assert hk._handle(VK_P, True) is False
    assert events == ["start"] and hk.mark is None


@pytest.mark.parametrize("mode", ["hold", "toggle"])
def test_escape_cancels_a_recording_and_only_that_escape_is_swallowed(mode):
    hk, events = _toggle()
    hk._config.mode = mode
    assert hk._handle(VK_ESC, True) is False  # no recording: passes through
    assert hk._handle(VK_ESC, False) is False
    _press_combo(hk)
    assert hk._handle(VK_ESC, True) is True
    assert hk._handle(VK_ESC, False) is True
    assert events == ["start", "cancel"] and not hk.active
    _release_combo(hk)
    assert events == ["start", "cancel"]  # releasing after a cancel does not stop anything
    assert hk._handle(VK_ESC, True) is False  # later Escape is the app's again


def test_escape_during_the_grace_window_cancels_the_pending_stop():
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    _press_combo(hk)
    assert hk._handle(VK_ESC, True) is True
    FakeTimer.fire_all()
    assert events == ["start", "cancel"]


def test_escape_inside_an_os_shortcut_is_not_a_cancel():
    for held in ({"ctrl", "shift"}, {"alt"}, {"ctrl"}):
        physical: set[str] = set()
        hk, events = _toggle(physical=physical)
        _press_combo(hk)
        _release_combo(hk)
        physical |= held  # pressed after the recording started
        assert hk._handle(VK_ESC, True) is False  # Ctrl+Shift+Esc, Alt+Esc, Ctrl+Esc reach Windows
        hk._handle(VK_ESC, False)
        assert events == ["start"] and hk.active


def test_escape_in_hold_ignores_the_binding_modifiers_but_not_others():
    hk, events = _toggle(physical={"ctrl", "alt"})
    hk._config.mode = "hold"
    _press_combo(hk)
    assert hk._handle(VK_ESC, True) is True and events == ["start", "cancel"]
    physical = {"ctrl", "alt"}
    hk2, events2 = _toggle(physical=physical)
    hk2._config.mode = "hold"
    _press_combo(hk2)
    physical.add("shift")  # Ctrl+Shift+Alt+Esc: another shortcut
    assert hk2._handle(VK_ESC, True) is False and events2 == ["start"]


def test_key_binding_marker_in_the_starting_hold_only_marks_even_after_the_key_is_released():
    hk, events = _toggle(key="A")
    hk._handle(LCTRL, True)
    hk._handle(LALT, True)
    hk._handle(VK_A, True)
    hk._handle(VK_A, False)  # trigger key up, Ctrl+Alt still down
    assert hk._handle(VK_P, True) is True
    assert events == ["start"] and hk.active and hk.mark == POLISH
    _release_combo(hk)
    hk._handle(LCTRL, True)
    hk._handle(LALT, True)
    hk._handle(VK_A, True)  # stop chord keeps the mark
    assert events == ["start", "stop"] and hk.mark == POLISH


def test_stop_gesture_time_is_taken_before_the_grace_window():
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    before = hk.stop_requested_at
    _press_combo(hk)
    assert hk.stop_requested_at > before and events == ["start"]  # set although the stop is still pending


def test_escape_after_the_recording_already_finished_is_not_swallowed():
    hk, events = _toggle()
    _press_combo(hk)
    _release_combo(hk)
    _press_combo(hk)
    FakeTimer.fire_all()  # the timer won the race
    assert hk._handle(VK_ESC, True) is False
    assert events == ["start", "stop"]


# -- hold mode: a foreign key joining the chord discards the recording --------------

VK_LEFT, VK_RIGHT, VK_MENU_MASK, LWIN = 0x25, 0x27, 0xE8, 0x5B


def _win_listener(mode: str = "hold", key: str = ""):
    """Default-style binding (Ctrl+Win) with paste, polish and command keys assigned."""
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "win"], key=key, mode=mode, paste_key="V", polish_key="P")
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        command_config=OsControlConfig(enabled=True, command_key="Y"),
        on_command=lambda: events.append("command"),
        on_cancel=lambda: events.append("cancel"),
        grace_s=0,
        timer_factory=FakeTimer,
        physical_modifiers=set,
    )
    return hk, events


def _hold_ctrl_win(hk):
    hk._handle(LCTRL, True)
    hk._handle(LWIN, True)


@pytest.mark.parametrize("vk", [LALT, RALT, LSHIFT])
def test_extra_modifier_discards_the_hold_recording(vk):
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    assert hk._handle(vk, True) is False  # the modifier still reaches the application
    assert events == ["start", "cancel"] and not hk.active
    hk._handle(vk, False)
    hk._handle(LWIN, False)
    hk._handle(LCTRL, False)
    assert events == ["start", "cancel"]  # releasing the chord afterwards stops nothing


@pytest.mark.parametrize("vk", [VK_A, VK_B, ord("1"), 0x70])
def test_extra_letter_discards_and_is_not_swallowed(vk):
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    assert hk._handle(vk, True) is False
    assert hk._handle(vk, False) is False
    assert events == ["start", "cancel"]


@pytest.mark.parametrize("vk", [VK_LEFT, VK_RIGHT])
def test_virtual_desktop_switch_never_records(vk):
    """Ctrl+Win+Left/Right: the arrow is a foreign key and cancels the hold."""
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    assert hk._handle(vk, True) is False
    assert events == ["start", "cancel"] and not hk.active


def test_discard_happens_once_and_a_second_foreign_key_does_nothing():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    hk._handle(VK_A, True)
    hk._handle(VK_B, True)
    assert events == ["start", "cancel"]


def test_assigned_keys_do_not_discard():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    assert hk._handle(ord("V"), True) is True  # paste key: marker, swallowed
    assert hk._handle(ord("V"), False) is True
    assert hk._handle(ord("P"), True) is True  # polish key
    assert hk._handle(ord("P"), False) is True
    assert hk._handle(VK_Y, True) is True  # command key
    assert "cancel" not in events and hk.active


def test_binding_key_does_not_discard():
    hk, events, _ = _listener("hold", ("ctrl", "shift"), "A")
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    hk._handle(VK_A, True)
    assert events == ["start"] and hk.active


def test_menu_mask_key_is_not_a_foreign_key():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    assert hk._handle(VK_MENU_MASK, True) is False
    assert events == ["start"] and hk.active


def test_second_binding_modifier_and_other_side_do_not_discard():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    hk._handle(RCTRL, True)
    hk._handle(0x5C, True)  # right Win
    assert events == ["start"] and hk.active


def test_unassigned_paste_key_counts_as_foreign():
    """An emptied paste key is not a marker any more, so its letter joins the chord like any other."""
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "win"], key="", mode="hold", paste_key="")
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        on_cancel=lambda: events.append("cancel"),
        grace_s=0,
    )
    _hold_ctrl_win(hk)
    hk._handle(ord("V"), True)
    assert events == ["start", "cancel"]


def test_foreign_key_before_the_chord_or_after_the_hold_is_plain_typing():
    hk, events = _win_listener()
    hk._handle(LCTRL, True)
    hk._handle(VK_A, True)  # not recording yet
    hk._handle(VK_A, False)
    hk._handle(LCTRL, False)
    _hold_ctrl_win(hk)
    hk._handle(LWIN, False)  # hold ends normally
    hk._handle(VK_A, True)
    assert events == ["start", "stop"]


@pytest.mark.parametrize("vk", [VK_LEFT, VK_RIGHT])
def test_toggle_virtual_desktop_switch_never_records(vk):
    """Ctrl+Win+Left/Right in Toggle: the arrow joins the start chord and discards, and is not swallowed."""
    hk, events = _win_listener("toggle")
    _hold_ctrl_win(hk)
    assert hk._handle(vk, True) is False
    assert events == ["start", "cancel"] and not hk.active


@pytest.mark.parametrize("vk", [LALT, VK_A])
def test_toggle_extra_modifier_or_letter_discards_while_the_chord_is_held(vk):
    hk, events = _win_listener("toggle")
    _hold_ctrl_win(hk)
    assert hk._handle(vk, True) is False
    hk._handle(vk, False)
    hk._handle(LWIN, False)
    hk._handle(LCTRL, False)
    assert events == ["start", "cancel"]


def test_toggle_typing_after_the_chord_was_released_keeps_the_recording():
    hk, events = _win_listener("toggle")
    _hold_ctrl_win(hk)
    hk._handle(LWIN, False)
    hk._handle(LCTRL, False)
    for vk in (VK_A, VK_LEFT, LALT):
        assert hk._handle(vk, True) is False
        hk._handle(vk, False)
    assert events == ["start"] and hk.active


def test_toggle_markers_binding_key_and_stop_keep_their_meaning():
    hk, events = _win_listener("toggle")
    _hold_ctrl_win(hk)
    assert hk._handle(ord("V"), True) is True  # marker in the starting hold: only marks
    assert hk.mark == PASTE_LAST and events == ["start"]
    hk._handle(ord("V"), False)
    hk._handle(LWIN, False)
    hk._handle(LCTRL, False)
    hk._handle(LCTRL, True)
    hk._handle(LWIN, True)  # stop chord
    assert events == ["start", "stop"]


def test_toggle_stop_chord_is_not_refused_by_a_held_extra_modifier():
    physical: set[str] = set()
    config = HotkeyConfig(modifiers=["ctrl", "win"], key="", mode="toggle")
    events: list[str] = []
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        grace_s=0,
        physical_modifiers=lambda: set(physical),
    )
    _hold_ctrl_win(hk)
    hk._handle(LWIN, False)
    hk._handle(LCTRL, False)
    physical.add("alt")
    _hold_ctrl_win(hk)
    assert events == ["start", "stop"]


def test_next_recording_works_after_a_discard():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    hk._handle(VK_LEFT, True)
    hk._handle(VK_LEFT, False)
    hk._handle(LWIN, False)
    hk._handle(LCTRL, False)
    _hold_ctrl_win(hk)
    assert events == ["start", "cancel", "start"] and hk.active


@pytest.mark.parametrize("vk", [0xAF, 0xB3, 0xA6, 0xE7, 0xE8, 0xFF, 0x01])
def test_non_typing_keys_do_not_discard(vk):
    """Volume, media, browser keys, VK_PACKET, the mask key and mouse buttons are not part of a shortcut."""
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    hk._handle(vk, True)
    assert events == ["start"] and hk.active


def test_injected_keys_do_not_discard():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    assert hk._handle(VK_A, True, injected=True) is False
    assert hk._handle(LALT, True, injected=True) is False
    assert events == ["start"] and hk.active
    hk._handle(VK_A, True)  # a real key still does
    assert events == ["start", "cancel"]


def test_a_foreign_key_after_the_window_is_plain_typing():
    hk, events = _win_listener()
    _hold_ctrl_win(hk)
    hk._active_since -= FOREIGN_KEY_WINDOW_S + 1  # the dictation has been running for a while
    assert hk._handle(VK_A, True) is False
    assert events == ["start"] and hk.active


def test_chord_with_an_extra_modifier_already_down_does_not_start():
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "win"], key="", mode="hold")
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        on_cancel=lambda: events.append("cancel"),
        physical_modifiers=lambda: {"alt", "ctrl", "win"},
    )
    _hold_ctrl_win(hk)
    assert events == []


def test_key_binding_with_an_extra_modifier_down_does_not_start_or_swallow():
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "shift"], key="A", mode="hold")
    hk = HotkeyListener(
        config,
        lambda: events.append("start"),
        lambda: events.append("stop"),
        physical_modifiers=lambda: {"alt", "ctrl", "shift"},
    )
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    assert hk._handle(VK_A, True) is False
    assert events == []


def test_toggle_chord_with_an_extra_modifier_already_down_does_not_start():
    events: list[str] = []
    config = HotkeyConfig(modifiers=["ctrl", "win"], key="", mode="toggle")
    hk = HotkeyListener(
        config, lambda: events.append("start"), lambda: events.append("stop"), physical_modifiers=lambda: {"alt"}
    )
    _hold_ctrl_win(hk)
    assert events == []
