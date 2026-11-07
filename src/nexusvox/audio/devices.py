"""Finding the input device a recording or the setup's level meter opens.

Imports sounddevice lazily (the caller passes the module in), so reading this module never needs PortAudio.

PortAudio lists devices once, when it starts; re-initialising it picks up new ones but invalidates every open stream
and every device index. Two rules keep that from hurting a recording:

* `refresh_devices` runs only while no stream is open and no other thread is opening one: whoever finds a device and
  opens a stream on it does both under `input_open_lock`, and counts the stream (`audio_state.stream_opened`) before
  it lets go of the lock.
* Looking a device up never restarts PortAudio unless the name is missing from the current list, and a restart that
  just happened is not repeated for `REFRESH_MIN_INTERVAL_S` (the Settings tab asks twice per visit).
"""

from __future__ import annotations

import logging
import threading
import time

from . import state as audio_state

logger = logging.getLogger(__name__)

# Re-entrant: `find_input_device` refreshes while its caller already holds the lock.
input_open_lock = threading.RLock()
REFRESH_MIN_INTERVAL_S = 2.0
_last_refresh = 0.0


def refresh_devices(sd, force: bool = False) -> None:
    """Make PortAudio notice devices plugged in since it started: it lists them once, at initialisation.

    Re-initialising invalidates open streams, so it only happens while none is open (dictation, meeting, meter),
    and not again within `REFRESH_MIN_INTERVAL_S` unless `force` is set (a named device that is not listed).
    """
    global _last_refresh
    with input_open_lock:
        if audio_state.streams_open():
            return
        if not force and time.monotonic() - _last_refresh < REFRESH_MIN_INTERVAL_S:
            return
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            logger.exception("Refreshing the audio device list failed")
        _last_refresh = time.monotonic()


def wasapi_index(sd) -> int | None:
    """Index of the Windows WASAPI host API, whose names are the full ones (MME cuts them at 31 characters)."""
    for index, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return index
    return None


def _named_input(sd, name: str) -> dict | None:
    wasapi = wasapi_index(sd)
    for dev in sd.query_devices():
        if dev["name"] == name and dev["max_input_channels"] > 0:
            if wasapi is None or dev["hostapi"] == wasapi:
                return dev
    return None


def find_input_device(sd, name: str, what: str = "Microphone") -> dict:
    """The named input device (on WASAPI when there is one); the default input when unnamed or gone.

    The current device list is searched first: PortAudio restarts only when the name is not in it (a device plugged
    in since start), so a recording never waits for a restart while its microphone is there.
    """
    if name:
        with input_open_lock:
            dev = _named_input(sd, name)
            if dev is None:
                refresh_devices(sd, force=True)
                dev = _named_input(sd, name)
            if dev is not None:
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
