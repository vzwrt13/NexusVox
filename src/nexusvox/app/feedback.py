"""How a dictation signals its state: the chimes and the waveform overlay."""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..config import FEEDBACK_MODES
from ..dictation.overlay import level_from_rms


def _play(beep: Callable[[], None]) -> None:
    """Play a feedback sound on its own thread, so neither the hook nor the loop waits for it."""
    threading.Thread(target=beep, daemon=True).start()


def _feedback_mode(config: object) -> str:
    """`sound`, `visual` or `both`: how dictation cues are shown; `sound` when the config says nothing."""
    mode = getattr(getattr(config, "feedback", None), "mode", "sound")
    return mode if mode in FEEDBACK_MODES else "sound"


class FeedbackCues:
    """The cue part of `NexusVoxApp`."""

    def _shows_overlay(self) -> bool:
        """Whether the waveform is wanted and its window is alive."""
        overlay = self._overlay
        return overlay is not None and not overlay.failed and _feedback_mode(getattr(self, "_config", None)) != "sound"

    def on_feedback_mode(self, mode: str) -> None:
        """The dashboard changed `[feedback].mode`: build the overlay window now, not mid-dictation."""
        if mode != "sound" and self._overlay is not None:
            self._overlay.warm_up()

    def _on_rms(self, rms: float) -> None:
        """Microphone level from the audio thread, for the waveform; only a recording shows it."""
        if self._overlay is not None:
            self._overlay.level(level_from_rms(rms))

    def _cue(self, beep: Callable[[], None] | None, state: str) -> None:
        """Signal a state the way `[feedback].mode` says: the chime, the overlay, or both.

        `state` is `RECORDING`, `PROCESSING`, `ERROR` or `CONFIRMED`. Meeting recordings and the phone
        never come through here for the overlay. A window that could not start or died leaves the sounds
        on, whatever the mode."""
        mode = _feedback_mode(getattr(self, "_config", None))
        visual = self._shows_overlay()
        if beep is not None and (mode != "visual" or not visual):
            _play(beep)
        if visual:
            self._overlay.show(state)
