"""The Alt menu mask in the injector's forced modifier release (Windows only: injector binds Win32)."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="injector binds Win32 at import")


def _sequence(alt_down):
    from nexusvox import injector

    return [
        (i._input.ki.wVk, bool(i._input.ki.dwFlags & injector.KEYEVENTF_KEYUP))
        for i in injector._release_inputs(alt_down)
    ]


def test_mask_precedes_alt_key_up_when_alt_is_down():
    from nexusvox import injector

    seq = _sequence(True)
    alt_up = seq.index((injector.VK_MENU, True))
    assert seq[alt_up - 2 : alt_up] == [(injector.VK_MENU_MASK, False), (injector.VK_MENU_MASK, True)]


def test_no_mask_when_alt_is_up():
    from nexusvox import injector

    assert all(vk != injector.VK_MENU_MASK for vk, _ in _sequence(False))


def test_win_held_also_gets_the_mask_before_its_key_up():
    from nexusvox import injector

    seq = [
        (i._input.ki.wVk, bool(i._input.ki.dwFlags & injector.KEYEVENTF_KEYUP))
        for i in injector._release_inputs({injector.VK_LWIN})
    ]
    assert seq.index((injector.VK_MENU_MASK, True)) < seq.index((injector.VK_LWIN, True))
    assert (injector.VK_MENU, True) not in seq


def test_only_held_modifiers_get_a_key_up():
    from nexusvox import injector

    seq = _sequence({injector.VK_CONTROL, injector.VK_SHIFT})
    assert seq == [(injector.VK_CONTROL, True), (injector.VK_SHIFT, True)]


def test_nothing_held_sends_nothing():
    from nexusvox import injector

    assert injector._release_inputs(set()) == []


def _capture_inputs(monkeypatch, held):
    """Run the Polish keyboard sequence (copy, then paste) with `held` modifiers down; return all injected events."""
    from nexusvox import injector

    sent = []
    monkeypatch.setattr(injector, "_send_inputs", lambda inputs, name: sent.extend(inputs) or True)
    monkeypatch.setattr(injector, "_send_ctrl_chord", lambda vk, scan, name: True)
    monkeypatch.setattr(injector, "_wait_for_physical_release", lambda timeout_s=1.5: True)
    monkeypatch.setattr(injector, "_held_modifiers", lambda: set(held))
    monkeypatch.setattr(injector.time, "sleep", lambda s: None)
    monkeypatch.setattr(injector.pyperclip, "paste", lambda: "selected")
    monkeypatch.setattr(injector, "set_text", lambda text: None)
    injector.copy_selection(0, timeout_s=0.1)  # Polish step 1: Ctrl+C
    injector.inject_text("polished", 0)  # Polish step 2: Ctrl+V
    return [(i._input.ki.wVk, bool(i._input.ki.dwFlags & injector.KEYEVENTF_KEYUP)) for i in sent]


def test_polish_sequence_sends_no_alt_key_up_when_everything_is_released(monkeypatch):
    """Regression: copy and paste each sent a stray Alt key-up, which toggled Notepad's menu bar."""
    from nexusvox import injector

    events = _capture_inputs(monkeypatch, held=set())
    assert not [e for e in events if e[0] in (injector.VK_MENU, injector.VK_MENU_MASK, injector.VK_LWIN)]


def test_polish_sequence_masks_a_forced_alt_release(monkeypatch):
    from nexusvox import injector

    events = _capture_inputs(monkeypatch, held={injector.VK_MENU})
    assert (injector.VK_MENU, True) in events
    assert events.index((injector.VK_MENU_MASK, True)) < events.index((injector.VK_MENU, True))


def test_polish_key_sequence_never_lets_a_lone_alt_release_reach_the_target(monkeypatch):
    """The whole Polish sequence, in both release orders: chord down, polish key down/up, chord up, then the
    copy (Ctrl+C) and the paste (Ctrl+V). Every event the target sees is recorded; an Alt key-up must always
    have a non-Alt key-down after the Alt key-down (that is what keeps Notepad's menu bar closed)."""
    from nexusvox import injector
    from nexusvox.config import HotkeyConfig
    from nexusvox.hotkey import POLISH, HotkeyListener

    LCTRL, LSHIFT, LALT, VK_P = 0xA2, 0xA0, 0xA4, ord("P")
    for order in ((LALT, LCTRL, LSHIFT), (LCTRL, LSHIFT, LALT), (LSHIFT, LALT, LCTRL)):
        target = []  # (vk, is_up) as the target window receives them
        pending = []  # events SendInput queued from inside the hook; they pass the hook again, then the target

        config = HotkeyConfig(modifiers=["ctrl", "shift", "alt"], key="", polish_key="P")
        hk = HotkeyListener(config, lambda: None, lambda: None)
        hk._alt_timeout_s = 30

        def reinject(vk, masked, pending=pending):
            if masked:
                pending += [(injector.VK_MENU_MASK, False), (injector.VK_MENU_MASK, True)]
            pending.append((vk, True))
            return True

        hk._alt_release = reinject
        hk._mask_sender = lambda pending=pending: pending.extend(
            [(injector.VK_MENU_MASK, False), (injector.VK_MENU_MASK, True)]
        )

        def key(vk, pressed, hk=hk, target=target, pending=pending):
            if not hk._handle(vk, pressed):
                target.append((vk, not pressed))
            while pending:  # queued synthetic events come back through the hook
                svk, up = pending.pop(0)
                if not hk._handle(svk, not up):
                    target.append((svk, up))

        for vk in (LCTRL, LSHIFT, LALT):
            key(vk, True)
        key(VK_P, True)
        key(VK_P, False)
        assert hk.mark == POLISH
        for vk in order:
            key(vk, False)

        # Then the app's side: copy and paste with everything physically up
        sent = []
        monkeypatch.setattr(injector, "_send_inputs", lambda inputs, name, sent=sent: sent.extend(inputs) or True)
        monkeypatch.setattr(injector, "_send_ctrl_chord", lambda vk, scan, name: True)
        monkeypatch.setattr(injector, "_wait_for_physical_release", lambda timeout_s=1.5: True)
        monkeypatch.setattr(injector, "_held_modifiers", lambda: set())
        monkeypatch.setattr(injector.time, "sleep", lambda s: None)
        monkeypatch.setattr(injector.pyperclip, "paste", lambda: "selected")
        monkeypatch.setattr(injector, "set_text", lambda text: None)
        injector.copy_selection(0, timeout_s=0.1)
        injector.inject_text("polished", 0)
        target += [(i._input.ki.wVk, bool(i._input.ki.dwFlags & injector.KEYEVENTF_KEYUP)) for i in sent]

        alt = (injector.VK_MENU, LALT)
        down_seen = other_since_down = False
        for vk, up in target:
            if vk in alt and not up:
                down_seen, other_since_down = True, False
            elif vk in alt and up:
                assert down_seen and other_since_down, f"lone Alt release reached the target (order {order})"
                down_seen = False
            elif down_seen and not up and vk not in (LCTRL, LSHIFT):
                other_since_down = True
        assert hk._alt_pending is None


def test_menu_mode_guard_sends_escape_only_when_a_menu_is_armed(monkeypatch):
    from nexusvox import injector

    sent = []
    monkeypatch.setattr(injector, "_send_inputs", lambda inputs, name: sent.extend(inputs) or True)
    monkeypatch.setattr(injector.time, "sleep", lambda s: None)
    monkeypatch.setattr(injector, "_foreground_in_menu_mode", lambda: False)
    injector._close_menu_mode("C")
    assert sent == []
    monkeypatch.setattr(injector, "_foreground_in_menu_mode", lambda: True)
    injector._close_menu_mode("C")
    assert [(i._input.ki.wVk, bool(i._input.ki.dwFlags & injector.KEYEVENTF_KEYUP)) for i in sent] == [
        (injector.VK_ESCAPE, False),
        (injector.VK_ESCAPE, True),
    ]
    sent.clear()
    monkeypatch.setattr(injector, "menu_guard_enabled", lambda: False)  # [hotkey].reinject_alt_release = false
    injector._close_menu_mode("C")
    assert sent == []
