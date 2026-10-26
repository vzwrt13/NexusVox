"""Which audio streams are open, kept apart from `audio.py` so reading it never needs PortAudio.

PortAudio lists devices once when it starts; re-initialising it picks up new ones but invalidates every open
stream, so the meeting device list only does that while nothing is open.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_open = 0


def stream_opened() -> None:
    global _open
    with _lock:
        _open += 1


def stream_closed() -> None:
    global _open
    with _lock:
        _open = max(0, _open - 1)


def streams_open() -> int:
    """How many streams (dictation and meeting microphone) are open right now."""
    return _open
