"""The dictation overlay's state, and how the app routes its cues by [feedback].mode."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nexusvox.overlay import (
    CONFIRMED,
    CONFIRMED_S,
    ERROR,
    ERROR_S,
    HIDDEN,
    PROCESSING,
    RECORDING,
    OverlayModel,
    level_from_rms,
)

try:
    from nexusvox import app as app_module
    from nexusvox.app import NexusVoxApp
except Exception:  # Windows-only imports (PortAudio, pynput hook, tray) on other platforms
    app_module = NexusVoxApp = None

needs_app = pytest.mark.skipif(NexusVoxApp is None, reason="nexusvox.app needs the Windows runtime")


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


def test_recording_then_processing_then_done():
    model = OverlayModel(Clock())
    assert model.state == HIDDEN
    model.show(RECORDING)
    assert model.state == RECORDING
    model.show(PROCESSING)
    assert model.state == PROCESSING
    model.show("done")
    assert model.state == HIDDEN


def test_levels_only_show_while_recording():
    model = OverlayModel(Clock())
    model.level(0.9)
    assert max(model.snapshot()[1]) == 0.0
    model.show(RECORDING)
    model.level(0.9)
    model.level(7.0)  # clamped
    assert model.snapshot()[1][-2:] == [0.9, 1.0]
    model.show(PROCESSING)
    model.level(0.5)
    assert model.snapshot()[1][-1] == 1.0


def test_a_new_recording_starts_with_a_flat_waveform():
    model = OverlayModel(Clock())
    model.show(RECORDING)
    model.level(0.8)
    model.show("done")
    model.show(RECORDING)
    assert max(model.snapshot()[1]) == 0.0


def test_an_error_shows_briefly_and_is_not_cut_short():
    clock = Clock()
    model = OverlayModel(clock)
    model.show(RECORDING)
    model.show(ERROR)
    model.show("done")  # the cycle's finally must not hide the error
    model.show(PROCESSING)
    assert model.state == ERROR
    clock.now += ERROR_S + 0.1
    assert model.state == HIDDEN


def test_a_recording_replaces_an_error():
    model = OverlayModel(Clock())
    model.show(ERROR)
    model.show(RECORDING)
    assert model.state == RECORDING


@pytest.mark.parametrize(("rms", "expected"), [(0.0, 0.0), (1e-5, 0.0), (0.1, 1.0), (0.01, 0.5)])
def test_level_scale(rms, expected):
    assert level_from_rms(rms) == pytest.approx(expected)


def _app(mode):
    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = SimpleNamespace(feedback=SimpleNamespace(mode=mode))
    app._overlay = MagicMock(failed=False)
    return app


@needs_app
@pytest.mark.parametrize(
    ("mode", "sound", "visual"), [("sound", True, False), ("visual", False, True), ("both", True, True)]
)
def test_cue_follows_the_feedback_mode(monkeypatch, mode, sound, visual):
    played = []
    monkeypatch.setattr(app_module, "_play", played.append)
    app = _app(mode)
    beep = object()
    app._cue(beep, RECORDING)
    assert bool(played) is sound
    assert app._overlay.show.called is visual


@needs_app
def test_cue_without_a_config_keeps_the_sound_only_behaviour(monkeypatch):
    played = []
    monkeypatch.setattr(app_module, "_play", played.append)
    app = NexusVoxApp.__new__(NexusVoxApp)  # built without __init__: no config, no overlay
    app._cue("beep", ERROR)
    assert played == ["beep"]


@needs_app
def test_visual_error_without_a_sound(monkeypatch):
    played = []
    monkeypatch.setattr(app_module, "_play", played.append)
    app = _app("visual")
    app._cue(None, ERROR)
    assert played == []
    app._overlay.show.assert_called_once_with(ERROR)


@needs_app
def test_microphone_level_reaches_the_overlay():
    app = _app("visual")
    app._on_rms(0.1)
    app._overlay.level.assert_called_once_with(1.0)


def test_a_confirmation_is_brief_and_not_cut_short():
    clock = Clock()
    model = OverlayModel(clock)
    model.show(PROCESSING)
    model.show(CONFIRMED)
    model.show("done")
    assert model.state == CONFIRMED
    clock.now += CONFIRMED_S + 0.1
    assert model.state == HIDDEN


def test_every_recording_gets_a_new_generation():
    model = OverlayModel(Clock())
    model.show(RECORDING)
    first = model.snapshot()[2]
    model.show(PROCESSING)
    model.show(RECORDING)  # back to back, never hidden in between
    assert model.snapshot()[2] == first + 1


@needs_app
def test_visual_mode_falls_back_to_the_sound_when_the_window_failed(monkeypatch):
    played = []
    monkeypatch.setattr(app_module, "_play", played.append)
    app = _app("visual")
    app._overlay.failed = True
    app._cue("beep", ERROR)
    assert played == ["beep"]
    app._overlay.show.assert_not_called()
    assert not app._shows_overlay()


@needs_app
def test_flag_confirmation_is_visible_in_visual_mode(monkeypatch):
    monkeypatch.setattr(app_module, "_play", lambda beep: None)
    app = _app("visual")
    app._cue("beep", CONFIRMED)
    app._overlay.show.assert_called_once_with(CONFIRMED)


@needs_app
def test_the_level_is_only_wanted_with_visual_feedback():
    assert not _app("sound")._shows_overlay()
    assert _app("both")._shows_overlay()


@needs_app
def test_switching_to_visual_builds_the_window():
    app = _app("sound")
    app.on_feedback_mode("sound")
    app._overlay.warm_up.assert_not_called()
    app.on_feedback_mode("both")
    app._overlay.warm_up.assert_called_once()
