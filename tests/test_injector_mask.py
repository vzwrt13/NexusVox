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

    seq = _sequence({injector.VK_MENU})
    alt_up = seq.index((injector.VK_MENU, True))
    assert seq[alt_up - 2 : alt_up] == [(injector.VK_MENU_MASK, False), (injector.VK_MENU_MASK, True)]


def test_no_mask_when_alt_is_up():
    from nexusvox import injector

    assert all(vk != injector.VK_MENU_MASK for vk, _ in _sequence({injector.VK_CONTROL}))


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


def test_only_right_win_down_gets_a_right_win_key_up(monkeypatch):
    from nexusvox import injector

    down = {injector.VK_RWIN}
    monkeypatch.setattr(injector.user32, "GetAsyncKeyState", lambda vk: 0x8000 if vk in down else 0, raising=False)
    seq = _sequence({injector.VK_LWIN})
    assert (injector.VK_RWIN, True) in seq and (injector.VK_LWIN, True) not in seq
