"""Visual dictation feedback: a small live waveform at the bottom centre of the screen.

`OverlayModel` is the state (hidden, recording, processing, error) and the level history; it is
plain Python and thread-safe, so the audio thread, the event loop and the window thread share it
and the tests drive it with a fake clock. `Overlay` owns the window: a Tk toplevel on its own
thread, started on the first cue, made click-through, topmost and non-activating with extended
window styles, so the dictation is still typed into the app the user was in.
"""

from __future__ import annotations

import ctypes
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from ctypes import wintypes

logger = logging.getLogger(__name__)

HIDDEN = "hidden"
RECORDING = "recording"
PROCESSING = "processing"
ERROR = "error"

ERROR_S = 1.4  # how long an error stays on screen
BARS = 28
FLOOR_DB = -60.0  # at or below this the bars are flat
CEILING_DB = -20.0  # speech this loud fills the pill


def level_from_rms(rms: float) -> float:
    """Map a linear RMS (0..1, float samples) to 0..1 on a decibel scale, so quiet speech still moves."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return min(1.0, max(0.0, (db - FLOOR_DB) / (CEILING_DB - FLOOR_DB)))


class OverlayModel:
    """What the overlay shows. `show` and `level` may be called from any thread."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state = HIDDEN
        self._error_until = 0.0
        self._levels: deque[float] = deque([0.0] * BARS, maxlen=BARS)

    @property
    def state(self) -> str:
        """The current state; an error whose time is up counts as hidden."""
        with self._lock:
            return self._current()

    def _current(self) -> str:
        if self._state == ERROR and self._clock() >= self._error_until:
            self._state = HIDDEN
        return self._state

    def show(self, state: str) -> None:
        """Enter `recording`, `processing` or `error`; `done` / `hidden` end it (an error runs out by itself)."""
        with self._lock:
            if state == RECORDING:
                self._levels.extend([0.0] * BARS)
                self._state = RECORDING
            elif state == PROCESSING:
                if self._current() != ERROR:
                    self._state = PROCESSING
            elif state == ERROR:
                self._state = ERROR
                self._error_until = self._clock() + ERROR_S
            elif self._current() != ERROR:  # done / hidden: never cut an error short
                self._state = HIDDEN

    def level(self, value: float) -> None:
        """Push a microphone level (0..1); only a recording shows it."""
        with self._lock:
            if self._state == RECORDING:
                self._levels.append(min(1.0, max(0.0, value)))

    def snapshot(self) -> tuple[str, list[float]]:
        with self._lock:
            return self._current(), list(self._levels)


# --- the window ------------------------------------------------------------------------------------

_WIDTH, _HEIGHT = 220, 44
_BOTTOM_MARGIN = 56
_TICK_MS = 33
_KEY = "#010101"  # the colour Tk makes transparent (the pill's corners)
_COLORS = {
    RECORDING: ("#18181b", "#8b8bff"),
    PROCESSING: ("#18181b", "#a1a1aa"),
    ERROR: ("#3a1416", "#f87171"),
}

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x20
_WS_EX_TOOLWINDOW = 0x80
_WS_EX_LAYERED = 0x80000
_WS_EX_NOACTIVATE = 0x08000000
_SW_HIDE = 0
_HWND_TOPMOST = -1
_SWP_NOACTIVATE = 0x10
_SWP_SHOWWINDOW = 0x40
_MONITOR_DEFAULTTOPRIMARY = 1


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def work_area_of_focused_window() -> tuple[int, int, int, int]:
    """The work area (left, top, right, bottom) of the monitor holding the focused window, else the primary one."""
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    monitor = user32.MonitorFromWindow(user32.GetForegroundWindow(), _MONITOR_DEFAULTTOPRIMARY)
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class Overlay:
    """The window around an `OverlayModel`. Nothing starts until the first cue that shows something."""

    def __init__(self, model: OverlayModel | None = None) -> None:
        self.model = model or OverlayModel()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._failed = False

    def show(self, state: str) -> None:
        self.model.show(state)
        if state in (RECORDING, PROCESSING, ERROR):
            self._ensure_window()

    def level(self, value: float) -> None:
        self.model.level(value)

    def warm_up(self) -> None:
        """Create the window now (hidden), so the first dictation does not pay for it."""
        self._ensure_window()

    def _ensure_window(self) -> None:
        with self._start_lock:
            if self._thread is not None or self._failed:
                return
            self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            self._failed = True
            logger.exception("The dictation overlay could not start; use the sounds instead")

    def _loop(self) -> None:
        import tkinter as tk  # lazy: only users of the visual feedback pay for it

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        previous = user32.GetForegroundWindow()
        root = tk.Tk()
        root.withdraw()
        root.update()
        # Creating a Tk root takes the focus for a moment, even withdrawn: give it back at once.
        if previous and user32.GetForegroundWindow() != previous:
            user32.SetForegroundWindow(previous)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", _KEY)
        root.configure(bg=_KEY)
        canvas = tk.Canvas(root, width=_WIDTH, height=_HEIGHT, bg=_KEY, highlightthickness=0, bd=0)
        canvas.pack()
        root.update_idletasks()

        user32.GetParent.restype = wintypes.HWND
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]  # fmt: skip
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        shown = False

        def place() -> None:
            """Show the window at the bottom centre of the focused window's monitor, without activating it."""
            # Tk rewrites the extended style when it maps the window, so set it right before every show.
            style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
            left, _top, right, bottom = work_area_of_focused_window()
            x = left + (right - left - _WIDTH) // 2
            y = bottom - _HEIGHT - _BOTTOM_MARGIN
            user32.SetWindowPos(hwnd, _HWND_TOPMOST, x, y, _WIDTH, _HEIGHT, _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

        def tick() -> None:
            nonlocal shown
            state, levels = self.model.snapshot()
            if state == HIDDEN:
                if shown:
                    user32.ShowWindow(hwnd, _SW_HIDE)
                    shown = False
            else:
                if not shown:
                    place()
                    shown = True
                self._draw(canvas, state, levels)
            root.after(_TICK_MS, tick)

        tick()
        root.mainloop()

    @staticmethod
    def _draw(canvas, state: str, levels: list[float]) -> None:
        background, accent = _COLORS[state]
        canvas.delete("all")
        r = _HEIGHT // 2
        canvas.create_oval(0, 0, _HEIGHT, _HEIGHT, fill=background, outline="")
        canvas.create_oval(_WIDTH - _HEIGHT, 0, _WIDTH, _HEIGHT, fill=background, outline="")
        canvas.create_rectangle(r, 0, _WIDTH - r, _HEIGHT, fill=background, outline="")
        if state == ERROR:
            canvas.create_line(r + 4, r, _WIDTH - r - 4, r, fill=accent, width=3, capstyle="round")
            return
        left, right = r + 2, _WIDTH - r - 2
        step = (right - left) / BARS
        now = time.monotonic()
        for i in range(BARS):
            if state == PROCESSING:  # a calm travelling wave: still working, not listening
                height = 4 + 8 * (0.5 + 0.5 * math.sin(now * 5 - i * 0.45))
            else:
                height = 3 + levels[i] * (_HEIGHT - 14)
            x = left + step * (i + 0.5)
            canvas.create_line(x, r - height / 2, x, r + height / 2, fill=accent, width=3, capstyle="round")
