"""The tray icon comes from the logo kit's tray drawings, with a red stroke while recording."""

import sys

import pytest

if sys.platform != "win32":  # pystray needs a display server on Linux; the tray is Windows-only
    pytest.skip("the tray is Windows-only", allow_module_level=True)

from nexusvox.system import tray  # noqa: E402


def _opaque_colours(img):
    raw = img.tobytes()
    return {tuple(raw[i : i + 3]) for i in range(0, len(raw), 4) if raw[i + 3] == 255}


@pytest.mark.parametrize("size", tray._SIZES)
@pytest.mark.parametrize("light", [False, True])
def test_every_tray_drawing_loads(size, light):
    img = tray._create_icon_image(light_taskbar=light, size=size)
    assert img.size == (size, size)


def test_idle_icon_has_violet_and_mint_and_no_red():
    colours = _opaque_colours(tray._create_icon_image(size=32))
    assert (108, 92, 231) in colours
    assert (0, 210, 160) in colours
    assert (255, 107, 107) not in colours


def test_recording_icon_replaces_mint_with_red():
    colours = _opaque_colours(tray._create_icon_image(active=True, size=32))
    assert (108, 92, 231) in colours
    assert (255, 107, 107) in colours
    assert (0, 210, 160) not in colours


def test_light_taskbar_uses_the_dark_tile():
    assert tray._create_icon_image(light_taskbar=True, size=16).getpixel((0, 0))[:3] == (13, 13, 16)
    assert tray._create_icon_image(size=16).getpixel((0, 0))[3] == 0


def test_recording_icon_on_a_light_taskbar_keeps_the_dark_tile_and_violet():
    img = tray._create_icon_image(active=True, light_taskbar=True, size=16)
    assert img.getpixel((0, 0))[:3] == (13, 13, 16)
    colours = _opaque_colours(tray._create_icon_image(active=True, light_taskbar=True, size=32))
    assert (108, 92, 231) in colours
    assert (13, 13, 16) in colours
    assert (0, 210, 160) not in colours
    assert any(r > 240 and g < 120 for r, g, _ in colours)


def test_icon_images_are_cached():
    assert tray._create_icon_image(True, False, 32) is tray._create_icon_image(True, False, 32)


def test_icon_size_is_one_of_the_drawn_sizes():
    assert tray._small_icon_size() in tray._SIZES
