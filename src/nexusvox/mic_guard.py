"""Mute every other app's microphone while push-to-talk is held.

While the hotkey is down, every WASAPI capture session on the default microphone that
does not belong to NexusVox (Discord, Teams, a browser call, ...) is muted per session,
never at the device level. On key-up each session goes back to the mute state it had
before, so a session the user muted on purpose stays muted.

Nothing may stay muted after NexusVox lets go of the key, exits, or dies:

- `release()` is idempotent and is called on key-up, in the `finally` of the recording
  cycle, from `atexit`, and from a watchdog timer that fires if key-up is never seen;
- while a hold is active the affected sessions are written to a small state file, and
  `restore_leftovers()` unmutes them on the next start if the previous process was killed.

The WASAPI access lives in `WasapiCaptureSessions`; `MicGuard` only sees objects that
satisfy `CaptureSession`, so the remember/restore logic is testable with fakes.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

STATE_FILENAME = "mic_guard_state.json"

# AudioSessionState values from audiosessiontypes.h
_SESSION_EXPIRED = 2


class CaptureSession(Protocol):
    """The subset of a WASAPI capture session the guard needs."""

    pid: int
    instance_id: str  # unique per live session (IAudioSessionControl2.GetSessionInstanceIdentifier)
    session_id: str  # stable per app across restarts (IAudioSessionControl2.GetSessionIdentifier)

    def get_mute(self) -> bool: ...

    def set_mute(self, muted: bool) -> None: ...


@dataclass(frozen=True)
class MutedEntry:
    """One session the guard muted; only sessions that were unmuted before are recorded."""

    pid: int
    instance_id: str
    session_id: str


class MicGuard:
    """Remember-mute-restore over a list of capture sessions.

    `enumerate` returns the current sessions; it is called on every hold and release so
    no COM object is ever shared across threads (release may run on the asyncio thread,
    a timer thread, or at exit).

    `hold_async()` / `release_async()` queue the work on one worker thread and return at
    once. `ISimpleAudioVolume.SetMute` costs ~40 ms per session, and pynput calls the
    hotkey callbacks inside the low-level keyboard hook, which must return quickly. The
    single worker keeps the order hold -> release even for a very short tap.
    """

    def __init__(
        self,
        enumerate: Callable[[], list[CaptureSession]],
        *,
        own_pids: set[int] | None = None,
        state_path: Path | None = None,
        watchdog_s: float = 30.0,
        still_held: Callable[[], bool] | None = None,
    ) -> None:
        self._enumerate = enumerate
        self._still_held = still_held
        self._own_pids = set(own_pids) if own_pids is not None else {os.getpid()}
        self._state_path = state_path
        self._watchdog_s = watchdog_s
        self._lock = threading.Lock()
        self._muted: list[MutedEntry] = []
        self._watchdog: threading.Timer | None = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mic-guard")

    @property
    def active(self) -> bool:
        return bool(self._muted)

    @property
    def muted_entries(self) -> list[MutedEntry]:
        return list(self._muted)

    # -- hold / release ---------------------------------------------------------------

    def hold_async(self) -> None:
        """Queue `hold()` on the worker thread; safe to call from the keyboard hook."""
        self._worker.submit(self._logged, self.hold)

    def release_async(self) -> None:
        """Queue `release()` on the worker thread, after any pending `hold()`."""
        self._worker.submit(self._logged, self.release)

    @staticmethod
    def _logged(fn: Callable[[], object]) -> None:
        try:
            fn()
        except Exception:
            logger.exception("Mic guard: %s failed", fn.__name__)

    def hold(self) -> list[MutedEntry]:
        """Mute every foreign session that is not already muted. Returns what was muted."""
        with self._lock:
            if self._muted:
                return list(self._muted)
            try:
                sessions = self._enumerate()
            except Exception:
                logger.exception("Mic guard: enumerating capture sessions failed, nothing muted")
                return []

            targets: list[tuple[CaptureSession, MutedEntry]] = []
            for s in sessions:
                if s.pid == 0 or s.pid in self._own_pids:
                    continue
                try:
                    if s.get_mute():
                        continue  # muted on purpose by the user; leave it alone
                except Exception:
                    logger.warning("Mic guard: cannot read mute state of pid %d, skipping", s.pid)
                    continue
                targets.append((s, MutedEntry(s.pid, s.instance_id, s.session_id)))

            # Persist before muting: if we die between the two, the next start unmutes
            # sessions that were unmuted anyway, which is harmless.
            entries = [e for _, e in targets]
            self._write_state(entries)

            muted: list[MutedEntry] = []
            for s, entry in targets:
                try:
                    s.set_mute(True)
                    muted.append(entry)
                except Exception:
                    logger.warning("Mic guard: muting pid %d failed", s.pid)
            self._muted = muted
            if not muted:
                self._clear_state()
            elif self._watchdog_s > 0:
                self._arm_watchdog()
            logger.info(
                "Mic guard: muted %d capture session(s) (pids %s), %d already muted, %d own",
                len(muted),
                [e.pid for e in muted],
                sum(1 for s in sessions if s.pid not in self._own_pids and s.pid != 0) - len(targets),
                sum(1 for s in sessions if s.pid in self._own_pids),
            )
            return list(muted)

    def release(self) -> int:
        """Restore every session muted by `hold()`. Idempotent. Returns how many were restored."""
        with self._lock:
            if self._watchdog is not None:
                self._watchdog.cancel()
                self._watchdog = None
            if not self._muted:
                return 0
            wanted = {e.instance_id for e in self._muted}
            restored = self._unmute_matching(instance_ids=wanted, session_ids=set())
            missing = len(wanted) - restored
            if missing:
                logger.warning("Mic guard: %d muted session(s) vanished before restore", missing)
            self._muted = []
            self._clear_state()
            logger.info("Mic guard: restored %d capture session(s)", restored)
            return restored

    def _arm_watchdog(self) -> None:
        self._watchdog = threading.Timer(self._watchdog_s, self._on_watchdog)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _on_watchdog(self) -> None:
        if not self.active:
            return
        if self._still_held is not None and self._safe_still_held():
            # A long dictation, not a lost key-up: check again later.
            with self._lock:
                if self.active:
                    self._arm_watchdog()
            return
        logger.warning("Mic guard: no key-up within %.0f s, restoring microphones", self._watchdog_s)
        self.release()

    def _safe_still_held(self) -> bool:
        try:
            return bool(self._still_held())
        except Exception:
            return False

    def warm_up(self) -> None:
        """Enumerate once so the first real hold does not pay comtypes' wrapper generation."""
        try:
            self._enumerate()
        except Exception:
            logger.exception("Mic guard: warm-up enumeration failed")

    # -- crash recovery ---------------------------------------------------------------

    def restore_leftovers(self) -> int:
        """Unmute sessions a previous, killed NexusVox left muted. Call once at startup."""
        entries = self._read_state()
        if not entries:
            return 0
        with self._lock:
            # Windows persists per-app mute under the session identifier, so a session that
            # was restarted since the crash is still muted: match on the stable id too.
            restored = self._unmute_matching(
                instance_ids={e.instance_id for e in entries},
                session_ids={e.session_id for e in entries},
            )
            self._clear_state()
        logger.info("Mic guard: restored %d microphone session(s) left muted by a previous run", restored)
        return restored

    def _unmute_matching(self, *, instance_ids: set[str], session_ids: set[str]) -> int:
        try:
            sessions = self._enumerate()
        except Exception:
            logger.exception("Mic guard: enumerating capture sessions failed, nothing restored")
            return 0
        restored = 0
        for s in sessions:
            if s.instance_id in instance_ids or s.session_id in session_ids:
                try:
                    s.set_mute(False)
                    restored += 1
                except Exception:
                    logger.warning("Mic guard: unmuting pid %d failed", s.pid)
        return restored

    # -- state file -------------------------------------------------------------------

    def _write_state(self, entries: list[MutedEntry]) -> None:
        if self._state_path is None or not entries:
            return
        try:
            payload = {"ts": time.time(), "entries": [e.__dict__ for e in entries]}
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            logger.warning("Mic guard: cannot write state file %s", self._state_path)

    def _clear_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Mic guard: cannot remove state file %s", self._state_path)

    def _read_state(self) -> list[MutedEntry]:
        if self._state_path is None or not self._state_path.exists():
            return []
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return [MutedEntry(int(e["pid"]), str(e["instance_id"]), str(e["session_id"])) for e in data["entries"]]
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Mic guard: state file %s is unreadable, ignoring", self._state_path)
            return []


# -- WASAPI backend -----------------------------------------------------------------------


class WasapiCaptureSession:
    """A live session on the default capture endpoint, wrapped for `CaptureSession`."""

    def __init__(self, control) -> None:
        from pycaw.pycaw import IAudioSessionControl2, ISimpleAudioVolume

        ctl2 = control.QueryInterface(IAudioSessionControl2)
        self._volume = control.QueryInterface(ISimpleAudioVolume)
        self.pid: int = ctl2.GetProcessId()
        self.instance_id: str = ctl2.GetSessionInstanceIdentifier() or ""
        self.session_id: str = ctl2.GetSessionIdentifier() or ""
        self.state: int = control.GetState()

    def get_mute(self) -> bool:
        return bool(self._volume.GetMute())

    def set_mute(self, muted: bool) -> None:
        self._volume.SetMute(1 if muted else 0, None)


def _ensure_com() -> None:
    """COM must be initialised on every thread that touches WASAPI. Safe to repeat."""
    import comtypes

    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        # RPC_E_CHANGED_MODE: the thread is already in an apartment, which is fine too.
        pass


def enumerate_capture_sessions() -> list[CaptureSession]:
    """All non-expired audio sessions on the default microphone (eCapture / eConsole).

    pycaw's `AudioUtilities.GetAllSessions()` only looks at the render endpoint, so the
    capture device is requested explicitly.
    """
    from comtypes import CLSCTX_ALL
    from pycaw.constants import EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities, IAudioSessionManager2

    _ensure_com()
    enumerator = AudioUtilities.GetDeviceEnumerator()
    device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, ERole.eConsole.value)
    manager = device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
    sessions = manager.GetSessionEnumerator()
    result: list[CaptureSession] = []
    for i in range(sessions.GetCount()):
        try:
            session = WasapiCaptureSession(sessions.GetSession(i))
        except Exception:
            logger.debug("Mic guard: skipping unreadable session %d", i, exc_info=True)
            continue
        if session.state != _SESSION_EXPIRED:
            result.append(session)
    return result


def create_mic_guard(
    state_dir: Path,
    *,
    watchdog_s: float = 30.0,
    still_held: Callable[[], bool] | None = None,
) -> MicGuard | None:
    """Build the production guard, or None (with a log line) when pycaw is unavailable."""
    try:
        import comtypes  # noqa: F401
        import pycaw  # noqa: F401
    except ImportError:
        logger.warning("Mic guard enabled but pycaw/comtypes are not installed; other apps will not be muted")
        return None
    guard = MicGuard(
        enumerate_capture_sessions,
        state_path=Path(state_dir) / STATE_FILENAME,
        watchdog_s=watchdog_s,
        still_held=still_held,
    )
    atexit.register(guard.release)
    return guard
