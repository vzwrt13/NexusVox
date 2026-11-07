"""Finding the input device a recording or the setup's level meter opens.

Imports sounddevice lazily (the caller passes the module in), so reading this module never needs PortAudio.
"""

from __future__ import annotations

import logging
import threading

from . import state as audio_state

logger = logging.getLogger(__name__)

_refresh_lock = threading.Lock()


def refresh_devices(sd) -> None:
    """Make PortAudio notice devices plugged in since it started: it lists them once, at initialisation.

    Re-initialising invalidates open streams, so it only happens while none is open (dictation, meeting, meter).
    """
    with _refresh_lock:
        if audio_state.streams_open():
            return
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            logger.exception("Refreshing the audio device list failed")


def wasapi_index(sd) -> int | None:
    """Index of the Windows WASAPI host API, whose names are the full ones (MME cuts them at 31 characters)."""
    for index, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return index
    return None


def find_input_device(sd, name: str, what: str = "Microphone") -> dict:
    """The named input device (on WASAPI when there is one); the default input when unnamed or gone."""
    if name:
        refresh_devices(sd)
        wasapi = wasapi_index(sd)
        for dev in sd.query_devices():
            if dev["name"] == name and dev["max_input_channels"] > 0:
                if wasapi is None or dev["hostapi"] == wasapi:
                    return dev
        logger.warning("%s %r not found; using the default input", what, name)
    return sd.query_devices(kind="input")


def input_device_names() -> list[str]:
    """`list_input_names` on the real PortAudio; empty without an audio stack."""
    try:
        import sounddevice as sd

        return list_input_names(sd)
    except Exception:
        logger.exception("Listing the input devices failed")
        return []


def list_input_names(sd) -> list[str]:
    """Names of the input devices to choose from (WASAPI ones when there is that host API), each once."""
    refresh_devices(sd)
    wasapi = wasapi_index(sd)
    names: list[str] = []
    for dev in sd.query_devices():
        if dev["max_input_channels"] > 0 and (wasapi is None or dev["hostapi"] == wasapi):
            if dev["name"] not in names:
                names.append(dev["name"])
    return names
