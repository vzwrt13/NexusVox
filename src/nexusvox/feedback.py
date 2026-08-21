"""Audio feedback for recording state changes.

Generates soft chime tones (layered harmonics with bell-like decay) played
through the default audio output via sounddevice.
"""

import numpy as np
import sounddevice as sd

_SAMPLE_RATE = 44100
_VOLUME = 0.30  # ~30 %


def _chime(frequency: float, duration: float, *, fade_in_ms: float = 80.0) -> np.ndarray:
    """Generate a soft chime by layering fundamental + octave + fifth.

    Each harmonic has its own amplitude and exponential decay rate so the
    sound blooms briefly then fades like a small bell or wind chime.
    """
    n_samples = int(_SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n_samples, endpoint=False, dtype=np.float32)

    # Harmonics: (freq multiplier, relative amplitude, decay rate)
    harmonics = [
        (1.0, 1.00, 3.0),  # fundamental — loudest, slowest decay
        (2.0, 0.35, 5.0),  # octave — adds brightness
        (1.5, 0.25, 4.5),  # fifth — adds warmth / shimmer
    ]

    wave = np.zeros(n_samples, dtype=np.float32)
    for mult, amp, decay in harmonics:
        wave += amp * np.exp(-decay * t) * np.sin(2.0 * np.pi * frequency * mult * t)

    # Normalize peak to 1.0 then apply volume
    peak = np.max(np.abs(wave))
    if peak > 0:
        wave = wave / peak * _VOLUME

    # Smooth raised-cosine fade-in to soften the attack
    fade_samples = int(_SAMPLE_RATE * fade_in_ms / 1000.0)
    if fade_samples > 0 and fade_samples < n_samples:
        fade = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade_samples, dtype=np.float32)))
        wave[:fade_samples] *= fade

    return wave


# Pre-compute chimes at import time so playback is instant.
_START_CHIME = _chime(880, 0.50)  # A5 — bright, airy chime
_STOP_CHIME = _chime(523, 0.50)  # C5 — warmer, settling chime
_FLAG_CHIME = _chime(660, 0.20)  # E5 — short, distinct confirmation


def beep_start() -> None:
    """Play a soft chime to indicate recording started."""
    sd.play(_START_CHIME, samplerate=_SAMPLE_RATE)
    sd.wait()


def beep_stop() -> None:
    """Play a soft chime to indicate recording stopped."""
    sd.play(_STOP_CHIME, samplerate=_SAMPLE_RATE)
    sd.wait()


def beep_flag() -> None:
    """Play a short chime to confirm transcription was flagged."""
    sd.play(_FLAG_CHIME, samplerate=_SAMPLE_RATE)
    sd.wait()
