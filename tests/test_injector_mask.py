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


def test_win_held_also_gets_the_mask_before_alt_key_up():
    from nexusvox import injector

    seq = [
        (i._input.ki.wVk, bool(i._input.ki.dwFlags & injector.KEYEVENTF_KEYUP)) for i in injector._release_inputs(True)
    ]
    assert seq.index((injector.VK_MENU_MASK, True)) < seq.index((injector.VK_LWIN, True))
