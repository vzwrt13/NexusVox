"""Main application orchestrator — wires hotkey, audio, transcriber, and injector.

`NexusVoxApp` builds the components and runs the main loop. Its behaviour lives in the other modules of this
package, one mixin each; they all work on the state set up here.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..audio.capture import AudioCapture
from ..audio.mic_guard import MicGuard, create_mic_guard
from ..audio.remote_mic import RemoteAudioSource, RemoteMicServer
from ..config import Config, resolve_device, save_config
from ..dashboard import open_dashboard, running_dashboard_api
from ..dictation.edit_watch import EditWatcher
from ..dictation.hotkey import HotkeyListener, MeetingHotkey, describe_hotkey
from ..dictation.overlay import Overlay
from ..extensions import load_extensions
from ..llm import unload as llm_unload
from ..llm import warm_up as llm_warm_up
from ..storage.db import Database
from ..system.tray import SystemTray
from ..transcription.transcriber import create_transcriber
from .commands import CommandHandling
from .common import logger
from .cycle import TranscriptionCycle
from .events import InputEvents
from .feedback import FeedbackCues, _feedback_mode
from .models import ModelSwitching, apply_startup_fallback
from .polish import SelectionPolish
from .records import RecordKeeping


class NexusVoxApp(
    InputEvents,
    TranscriptionCycle,
    CommandHandling,
    SelectionPolish,
    ModelSwitching,
    RecordKeeping,
    FeedbackCues,
):
    """Orchestrates the push-to-talk transcription pipeline."""

    # When the hotkey last went down: a take starts here, not when the connection to the server is up.
    _press_time = 0.0
    _overlay: Overlay | None = None  # the visual feedback window; a test App built without __init__ has none
    _main_task: asyncio.Task | None = None  # what Quit cancels; set in `_run`

    def __init__(self, config: Config) -> None:
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

        # Resolve device once at startup. A configured model that cannot start (a custom server
        # without a URL) falls back to Parakeet without rewriting the user's TOML.
        self._device = resolve_device(config.inference.device)
        logger.info(
            "Resolved compute device: %s (requested: %s)",
            self._device,
            config.inference.device,
        )
        apply_startup_fallback(config.inference, self._device)

        # Optional extension packages (entry points); a broken one is logged and skipped. In the background,
        # so a heavy package never delays the hotkey; the dashboard waits for it when it needs the registry.
        threading.Thread(target=load_extensions, name="load-extensions", daemon=True).start()

        # Components
        self._audio = AudioCapture(config.audio)
        self._overlay = Overlay()
        if _feedback_mode(config) != "sound":
            self._overlay.warm_up()
        self._audio.on_rms = self._on_rms
        self._audio.rms_wanted = self._shows_overlay
        self._transcriber = create_transcriber(
            config.inference,
            device=self._device,
            auto_fallback=config.inference.device == "auto",
        )
        self._db = Database(config.database)
        # Statistics writes (dictionary hits, learn-from-edits outcomes) run here, one at a time and in order,
        # so a slow or locked database never delays the paste or the hotkey.
        self._stats_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stats")
        # Reads the field of the last paste and queues the user's corrections as dictionary
        # suggestions. `[learn_from_edits].enabled` is checked per paste, so the toggle is live.
        self._edit_watcher = EditWatcher(
            self._db.add_dictionary_suggestions,
            self._db.get_dictionary_entries,
            on_outcome=lambda outcome: self._stats_pool.submit(self._store_stat, self._db.record_learn_event, outcome),
        )

        self._hotkey = HotkeyListener(
            config=config.hotkey,
            on_activate=self._on_hotkey_activate,
            on_deactivate=self._on_hotkey_deactivate,
            command_config=config.os_control,
            on_command=self._on_hotkey_command,
            on_cancel=self._on_hotkey_cancel,
        )
        # The latest hotkey stop: (marker at the stop, dropped by Escape or a foreign key, time of the stop gesture).
        # Captured in the stop callback, so a quick next take cannot reset what this one was marked as before
        # its cycle reads it. One slot, not a queue: start/stop events collapse when several presses arrive while
        # a cycle runs, and a queue would then go out of step with its cycles for good.
        self._stop_info: tuple[str | None, bool, float | None] | None = None

        # Meeting recording has its own toggle hotkey; dictation is untouched while a meeting runs.
        self._meeting_hotkey = MeetingHotkey(config.meeting, self._on_meeting_hotkey)
        self._meeting_toggle_lock = threading.Lock()

        # Mutes other apps' microphone sessions while the hotkey is held. Always built
        # (None only when pycaw is missing); `[mic_guard].enabled` is checked per hold so
        # the dashboard toggle is live. Release is unconditional and idempotent.
        self._mic_guard: MicGuard | None = create_mic_guard(
            Path(config.database.path).parent,
            watchdog_s=config.mic_guard.watchdog_s,
            still_held=self._hotkey.still_recording,
        )

        self._tray = SystemTray(
            on_quit=self._on_quit,
            on_toggle_translate=self._toggle_translate,
            get_translate=lambda: self._config.translator.enabled,
            on_open_dashboard=self._open_dashboard,
        )

        # Signals between hotkey thread and asyncio loop
        self._record_start_event: asyncio.Event | None = None
        self._record_stop_event: asyncio.Event | None = None

        # A phone recording in place of the microphone ([remote_mic]). The server is
        # built in `_run`, on the loop it serves from. `_remote_recording` is set from the
        # phone's start until the cycle is over; the hotkey is ignored meanwhile. The text
        # goes back to the phone (`_remote_text`) instead of being typed here.
        self._remote_audio = RemoteAudioSource()
        self._remote_mic: RemoteMicServer | None = None
        self._remote_recording = False
        self._remote_cancelled = False
        self._remote_text = ""
        self._cycle_running = False
        # What the last dictation pasted, for the paste key. In memory only: the saved
        # transcript is the spoken text, which differs once it was translated.
        self._last_typed = ""
        self._last_typed_rewritten = False  # True when it is a Polish or extension reply, not a dictation

        # Model switch state
        self._switch_status: str = "idle"
        self._switch_error: str | None = None

    def _open_dashboard(self) -> None:
        """Open the dashboard window."""
        open_dashboard(self._db, self._config, app=self)

    def _open_dashboard_logged(self) -> None:
        """`_open_dashboard` for a thread nobody waits for: a failure (the port is taken, no browser) is logged,
        not left on a future that is never read."""
        try:
            self._open_dashboard()
        except Exception:
            logger.exception("The dashboard could not be opened for the first-run setup")

    def open_dashboard_from_second_start(self) -> None:
        """A second start of NexusVox rang the doorbell: show the dashboard of this instance."""
        logger.info("A second NexusVox start was detected; opening the dashboard of this instance")
        self._open_dashboard()

    def _toggle_translate(self) -> None:
        """Switch translate-before-inject on or off, and keep it across restarts."""
        self._config.translator.enabled = not self._config.translator.enabled
        save_config(self._config)
        logger.info("Translate before inject: %s", "on" if self._config.translator.enabled else "off")

    def _on_quit(self) -> None:
        """Handle quit from system tray: end the main task at once, whatever it waits for.

        Not `loop.stop()`: that leaves `_run` unfinished, so `_shutdown` never runs (an open meeting would keep
        its WAV files without proper headers) and `asyncio.run` raises.
        """
        self._running = False
        if self._loop is not None and self._main_task is not None:
            self._loop.call_soon_threadsafe(self._main_task.cancel)

    async def _run(self) -> None:
        """Serve until Quit, then clean up."""
        self._loop = asyncio.get_event_loop()
        self._main_task = asyncio.current_task()
        self._running = True
        self._record_start_event = asyncio.Event()
        self._record_stop_event = asyncio.Event()
        try:
            await self._serve()
        except asyncio.CancelledError:
            logger.info("Quit requested")
        await self._shutdown()

    async def _serve(self) -> None:
        """Start the components, then run a transcription cycle for each hotkey press."""
        # Start components
        self._hotkey.start()
        self._meeting_hotkey.start()
        self._tray.start()

        logger.info(
            "NexusVox started. %s %s to talk.",
            "Press" if self._config.hotkey.mode == "toggle" else "Hold",
            describe_hotkey(self._config.hotkey),
        )

        if not self._config.setup_done:
            # A fresh install: the tray icon is easy to miss, so the dashboard opens by itself, on the first-run setup.
            logger.info("First start: opening the dashboard for the first-run setup")
            self._loop.run_in_executor(None, self._open_dashboard_logged)

        if self._mic_guard is not None:
            # Repair what a killed previous run left muted, even if the guard has been
            # switched off since. The warm-up (one-off COM wrapper cost, otherwise paid
            # on the first key-down) is only worth it while the guard is enabled.
            await self._loop.run_in_executor(None, self._mic_guard.restore_leftovers)
            if self._config.mic_guard.enabled:
                await self._loop.run_in_executor(None, self._mic_guard.warm_up)

        if self._config.cleanup.level != "off":
            # Auto cleanup gives up after [cleanup].timeout_s; a cold model would cost the first dictations.
            llm_warm_up(self._config.llm)

        # For in-process transcribers (Whisper on CPU), eagerly load the model
        # so the first hotkey press doesn't pay the full load + download latency.
        # A custom server is remote and has nothing to preload.
        # Engine "auto" on a GPU machine warms up the Docker server (or the CPU model while it is down).
        if self._warms_up():
            asyncio.create_task(self._preload_transcriber())

        if self._config.remote_mic.enabled:
            self._remote_mic = RemoteMicServer(
                self._config.remote_mic,
                self._remote_audio,
                self._on_remote_start,
                self._on_remote_stop,
                self._on_remote_cancel,
            )
            asyncio.create_task(self._remote_mic.run())

        # Main loop: wait for hotkey press, run transcription cycle
        while self._running:
            await self._record_start_event.wait()
            self._record_start_event.clear()

            if not self._running:
                break

            self._cycle_running = True
            try:
                await self._transcription_cycle(remote=self._remote_recording)
            finally:
                self._cycle_running = False
                if self._remote_recording:
                    self._remote_recording = False
                    # A cycle that failed after the phone's stop leaves it set; the next
                    # hotkey cycle would take it as a release and skip itself.
                    self._record_stop_event.clear()
                    if self._remote_mic is not None:
                        self._remote_mic.finished(self._remote_text)

    async def _shutdown(self) -> None:
        """Clean up all components."""
        logger.info("Shutting down...")
        self._hotkey.stop()
        self._meeting_hotkey.stop()
        api = running_dashboard_api()
        if api is not None:
            api.shutdown_meeting()
        self._tray.stop()
        self._audio.stop()
        self._edit_watcher.stop()
        self._stats_pool.shutdown(wait=True)
        if self._remote_mic is not None:
            await self._remote_mic.close()
        if self._mic_guard is not None:
            self._mic_guard.release()
        await self._transcriber.disconnect()
        # The model stays loaded while the app runs ([llm].keep_alive = -1); give its memory back on quit.
        await asyncio.get_running_loop().run_in_executor(None, llm_unload, self._config.llm)

    def run(self) -> None:
        """Start the application."""
        asyncio.run(self._run())
