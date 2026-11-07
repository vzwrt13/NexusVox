"""One transcription cycle: connect, record, transcribe, then type the text or hand it on."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time

from ..audio.capture import AudioCapture
from ..audio.remote_mic import RemoteAudioSource
from ..dashboard import running_dashboard_api
from ..dictation.clipboard import ClipboardError
from ..dictation.feedback import beep_error, beep_start, beep_stop
from ..dictation.hotkey import COMMAND, PASTE_LAST, POLISH
from ..dictation.injector import inject_text
from ..dictation.lang_detect import stored_language
from ..dictation.os_commands import parse_nexus_command
from ..dictation.overlay import ERROR, PROCESSING, RECORDING
from ..dictation.pipeline import process_text
from ..extensions import HOOK_TIMEOUT_S
from ..system.logsetup import Text
from .common import QUICK_TAP_S, logger
from .feedback import _play
from .records import _Recording

# A toggle take shorter than this is an accidental double press: dropped, with a neutral cue.
MIN_TOGGLE_TAKE_S = 0.3
# Hold mode: the start chime and the overlay appear this long after the key press, so a longer shortcut that
# begins with the chord (Ctrl+Win+Alt+letter) discards the recording while it is still silent. Audio is captured
# from the first moment; only the cue waits.
START_CUE_DELAY_S = 0.15
# A key press older than this before the recording started is not the start of this take.
MAX_TAKE_LEAD_S = 10.0


class TranscriptionCycle:
    """The cycle part of `NexusVoxApp`."""

    @staticmethod
    def _begin_take(start: float) -> int | None:
        """Tell a running meeting that a dictation take began; the microphone records it too, and what happens to
        it is decided when the meeting is transcribed. Blocking (locks, file write): run it in an executor."""
        api = running_dashboard_api()
        return api.begin_dictation(start) if api is not None else None

    @staticmethod
    def _end_take(token: int | None, start: float, end: float) -> None:
        """Tell a running meeting that the take ended. Blocking, like `_begin_take`."""
        api = running_dashboard_api()
        if api is not None:
            api.end_dictation(token, start, end)

    async def _transcription_cycle(self, remote: bool = False) -> None:
        """Run one full transcription cycle: connect, record, transcribe, inject.

        `remote` records from the phone ([remote_mic]) instead of the microphone; it is
        never a command recording and leaves the hotkey state alone.
        """
        recording_stopped = False
        pressed_at = self._press_time  # `_press_time` moves on with the next press, while this take still runs
        loop = asyncio.get_running_loop()
        take: tuple[int | None, float] | None = None  # (meeting token, start) while a take is open
        audio: AudioCapture | RemoteAudioSource | None = None  # set once recording started
        stream_task: asyncio.Task | None = None
        cue_timer: asyncio.TimerHandle | None = None  # the delayed start cue of a hold recording
        cue_shown = False

        def show_start_cue(force: bool = False) -> None:
            """The hold recording's start signal; the timer skips it once the recording ended or was dropped."""
            nonlocal cue_shown
            if cue_shown or not (force or (self._hotkey.active and not self._record_stop_event.is_set())):
                return
            cue_shown = True
            self._tray.set_active(True)
            self._cue(beep_start, RECORDING)

        async def end_take(end: float) -> None:
            nonlocal take
            if take is not None:
                (token, start), take = take, None
                await loop.run_in_executor(None, self._end_take, token, start, end)

        try:
            # Connect to inference server BEFORE signalling readiness so the
            # WebSocket handshake doesn't race against hotkey release.
            try:
                await self._transcriber.connect()
            except Exception:
                if remote:
                    raise
                logger.exception("Could not reach the transcription server")
                with contextlib.suppress(Exception):
                    await self._transcriber.disconnect()
                await self._offline_stop(pressed_at)
                return

            # A stop left from an earlier tap while the keys are down again: this press is the take, so record it.
            # Skipping would leave its release as a stop with no cycle to read it, and every later take would
            # find a stale stop and be skipped too.
            if not remote and self._record_stop_event.is_set() and self._hotkey.active:
                self._record_stop_event.clear()
                self._next_stop()
                logger.info("Dropped a stop from an earlier press, the hotkey is held again")

            # Clear any stop event that arrived while we were connecting.
            # If the user already released the keys, skip this cycle. A phone recording
            # is not skipped: its audio was buffered while we connected, and the stop
            # event stays set so the wait below returns at once.
            if not remote and self._record_stop_event.is_set():
                self._record_stop_event.clear()
                skip_mark, skip_cancelled, _ = self._next_stop()
                logger.info("Hotkey released before recording could start, skipping")
                await self._transcriber.disconnect()
                await self._hide_os_control()
                if skip_cancelled:
                    return
                if skip_mark is None and self._hotkey.mode == "toggle":
                    self._cue(beep_stop, PROCESSING)  # an accidental double press is not silent
                if skip_mark == PASTE_LAST:
                    # A quick tap of the paste chord (or one queued behind a running
                    # cycle) needs no recording: paste the last text all the same.
                    await self._paste_last_text()
                elif skip_mark == POLISH:
                    # A quick tap of the polish chord: nothing was said, so the default
                    # instruction; there is no recording, so its row has no audio.
                    await self._polish_selection("", None)
                return

            # Now we are ready — start recording
            logger.info("Recording started")
            start_time = time.monotonic()
            audio = self._remote_audio if remote else self._audio
            if remote:
                # A phone recording cues before the microphone opens, as it always did.
                self._tray.set_active(True)
                _play(beep_start)
            audio.start()
            if not remote:
                # Hold and Toggle: audio runs from the first moment, but the tray, the chime and the overlay
                # wait a moment, so a longer shortcut that starts with the chord (Ctrl+Win+Alt+letter,
                # Ctrl+Win+Arrow) is discarded before anything shows. Counted from the key press, so the
                # connection and the microphone opening already used up part of the wait. (The mic guard
                # mutes at the press and is released again by the discard.)
                wait_s = max(0.0, START_CUE_DELAY_S - (time.monotonic() - self._press_time))
                cue_timer = loop.call_later(wait_s, show_start_cue)
            if not remote:
                # From the key press if the connection took a while, so the first words are inside the range.
                pressed = (
                    self._press_time if start_time - MAX_TAKE_LEAD_S <= self._press_time <= start_time else start_time
                )
                take = (await loop.run_in_executor(None, self._begin_take, pressed), pressed)

            # Stream audio chunks until recording stops
            stream_task = asyncio.create_task(self._stream_audio(audio))

            # Wait for hotkey release
            # The phone ends its own recording; a cap here would send `done` while it still streams.
            cap_s = None if remote else self._config.hotkey.max_recording_s or None
            try:
                await asyncio.wait_for(self._record_stop_event.wait(), cap_s)
                self._record_stop_event.clear()
                mark, cancelled, stop_at = (None, False, None) if remote else self._next_stop()
            except TimeoutError:
                # Nothing ends a forgotten recording: stop it as if the user had, and say so.
                logger.warning("Recording stopped after the %s s maximum, processing what was said", cap_s)
                mark, cancelled, stop_at = (None, False, None) if remote else (self._hotkey.mark, False, None)
                if not remote:
                    self._hotkey.cancel()
                    if self._mic_guard is not None:
                        self._mic_guard.release_async()
            recording_stopped = True
            if cue_timer is not None:
                cue_timer.cancel()
                if not cancelled:
                    show_start_cue(force=True)  # a tap shorter than the delay still gets its start/stop pair

            # Stop audio (sends None sentinel, ending the stream task)
            audio.stop()
            audio_buffer = await stream_task

            if cancelled:
                logger.info("Recording discarded (Escape or a foreign key), nothing to transcribe")
                self._tray.set_active(False)
                await self._transcriber.disconnect()
                await self._hide_os_control()
                return

            stop_time = time.monotonic()
            duration_ms = int((stop_time - start_time) * 1000)
            await end_take(stop_time)
            logger.info("Recording stopped (duration=%dms)", duration_ms)

            if remote and self._remote_cancelled:
                logger.info("Remote recording cancelled, nothing to transcribe")
                self._tray.set_active(False)
                await self._transcriber.disconnect()
                return

            self._tray.set_active(False)
            if remote:
                _play(beep_stop)
            else:
                self._cue(beep_stop, PROCESSING)

            # Measured to the stop gesture, not to the end of the grace window that follows a modifier-only chord.
            take_s = (stop_at - start_time) if stop_at else duration_ms / 1000
            if not remote and mark is None and self._hotkey.mode == "toggle" and take_s < MIN_TOGGLE_TAKE_S:
                logger.info("Toggle take under %d ms, dropped", MIN_TOGGLE_TAKE_S * 1000)
                await self._transcriber.disconnect()
                return

            if mark == PASTE_LAST:
                # The paste key was tapped during the hold: drop this recording and paste
                # the last text again instead of transcribing.
                await self._transcriber.disconnect()
                await self._paste_last_text()
                return

            # Finalize transcription
            try:
                result = await self._transcriber.finish()
            except Exception:
                if remote or mark != POLISH or stop_at is None or stop_at - pressed_at >= QUICK_TAP_S:
                    raise
                # A polish tap needs no transcript (HTTP backends only fail here, not in connect()).
                logger.exception("Transcription failed; polishing with the default instruction")
                await self._transcriber.disconnect()
                await self._polish_selection("", None)
                return
            transcribe_ms = int((time.monotonic() - stop_time) * 1000)
            logger.info("Transcript received after %dms", transcribe_ms)

            # The engine that made this transcript, read now: an auto engine may switch for the next take meanwhile.
            device = self._transcriber.device
            recording = _Recording(audio_buffer, duration_ms, result.confidence, transcribe_ms, device)

            if mark == COMMAND:
                # A command recording (command key pressed during the hold) is never
                # typed: the transcript goes to os-control, which also hides
                # its widget again when there is nothing to act on.
                command_text = result.text.strip()
                reply = await asyncio.get_running_loop().run_in_executor(
                    None, self._send_os_command, command_text or "::hide"
                )
                if reply is None or reply.startswith("error:"):
                    self._cue(beep_error, ERROR)
                if command_text:
                    await loop.run_in_executor(
                        None, self._save_command, recording, f"[os-control] {command_text}", command_text
                    )
                await self._transcriber.disconnect()
                return

            if mark == POLISH:
                # The polish key was tapped during the hold: what was said (maybe nothing)
                # is an instruction for the selected text, not text to type.
                await self._transcriber.disconnect()
                await self._polish_selection(result.text.strip(), recording)
                return

            raw_text = result.text.strip()
            if not raw_text:
                logger.info("Empty transcription, nothing to inject")
                return
            loop = asyncio.get_running_loop()

            self._hook_budget_s = HOOK_TIMEOUT_S
            # A phone recording only wants its text back, never a PC action.
            if not remote:
                nexus_cmd = parse_nexus_command(raw_text)
                if nexus_cmd is not None and nexus_cmd.action == "flag" and self._config.experimental.flag_command:
                    self._flag_last_transcription()
                    await self._transcriber.disconnect()
                    return
                label = await self._run_command(raw_text, nexus_cmd)
                if label is not None:
                    await loop.run_in_executor(None, self._save_command, recording, label, raw_text)
                    await self._transcriber.disconnect()
                    return

            processed = await loop.run_in_executor(
                None,
                functools.partial(
                    process_text,
                    raw_text,
                    self._config,
                    self._db.get_dictionary_entries(),
                    remote=remote,
                    extend_fn=None if remote else self._extend_dictation,
                    protect_fn=functools.partial(self._protect_dictation, remote=remote),
                ),
            )
            done_at = None  # when the text was in place; the clipboard restore after it is not latency
            target_app = None  # process name of the app the text lands in; never the window title
            if remote:
                # The phone inserts it into its own focused field.
                self._remote_text = processed.typed
            else:
                try:
                    done_at, target_app = await loop.run_in_executor(
                        None, self._paste, processed.typed, self._config.injection_delay_ms, processed.press_enter
                    )
                    pasted = True
                except ClipboardError as exc:
                    # The transcript is still saved below, and the paste key pastes the text.
                    logger.warning("Could not paste the dictation (%s); the paste key pastes it", exc)
                    self._cue(beep_error, ERROR)
                    pasted = False
                if processed.typed:  # an Enter-only dictation keeps the last text
                    self._last_typed = processed.typed
                    self._last_typed_rewritten = False
                if self._config.learn_from_edits.enabled and processed.typed:
                    if not pasted:
                        self._edit_watcher.skip("clipboard")
                    elif not processed.learnable:
                        self._edit_watcher.skip("processed")
                    else:
                        self._edit_watcher.watch(
                            processed.typed, self._config.learn_from_edits.window_s, processed.spoken
                        )
            latency_ms = int(((done_at or time.monotonic()) - stop_time) * 1000)
            # lingua loads its models on first use: keep that off the event loop.
            language = await loop.run_in_executor(None, stored_language, raw_text)
            save = functools.partial(
                self._db.save_transcription,
                text=processed.saved,
                language=language,
                duration_ms=duration_ms,
                confidence=result.confidence,
                model=self._transcriber.model,
                translated=processed.translation.translated,
                translate_source=processed.translation.source,
                translate_ms=processed.translation.ms,
                translate_error=processed.translation.error,
                transcribe_ms=transcribe_ms,
                latency_ms=latency_ms,
                cleanup_status=processed.cleanup.status,
                cleanup_ms=processed.cleanup.ms,
                llm_model=processed.cleanup.llm_model,
                llm_url=processed.cleanup.llm_url,
                cleanup_level=processed.cleanup.level,
                transcribe_device=device,
                app=target_app,
            )
            record = await loop.run_in_executor(None, save)
            logger.info(
                "%s after %dms: %s", "Sent to phone" if remote else "Injected", latency_ms, Text(processed.typed)
            )
            self._stats_pool.submit(self._store_stat, self._db.record_dictionary_hits, processed.dictionary_hits)
            await loop.run_in_executor(None, self._store_audio, audio_buffer, record.id)

        except Exception:
            logger.exception("Transcription cycle failed")
            await self._abort_recording(audio, stream_task, recording_stopped)
            self._tray.set_active(False)
            if not remote:
                self._cue(None, ERROR)
            if not recording_stopped and not remote:
                # The recording this intent belonged to is gone. Without this, toggle
                # mode would treat the next press as "stop" (and keep the mic guard
                # muted); hold mode would queue a stale stop that skips the next cycle.
                self._hotkey.cancel()
            if not remote:
                # `_hotkey.command` may still describe the last hotkey recording.
                await self._hide_os_control()
            await self._transcriber.disconnect()
        finally:
            if cue_timer is not None:
                cue_timer.cancel()
            await end_take(time.monotonic())  # a take that died before its stop is stored too
            # Key-up already queued a release; this covers a cycle that died mid-hold.
            # Skip it while the hotkey is held again: the user may already be in the
            # next dictation (pressed during transcription), and its hold must stand.
            if self._mic_guard is not None and not self._hotkey.active:
                self._mic_guard.release_async()
            if self._overlay is not None and not remote:
                if not self._hotkey.active:
                    self._overlay.show("done")  # the text is typed (or nothing was); an error stays its moment
                elif self._shows_overlay():
                    # Held again: the next take starts when its connection is up, but the user is speaking now.
                    self._overlay.show(RECORDING)

    def _paste(self, text: str, delay_ms: int, press_enter: bool) -> tuple[float, str | None]:
        """Type the text at the cursor (runs in the executor); also the process name of the app it lands in.

        The app is read right before the paste, and only a paste that went through returns it, so a dictation
        the clipboard refused is never credited to an app. Only the process name is kept, never the title.
        """
        process = self._active_app().process.lower() if text else ""
        return inject_text(text, delay_ms, press_enter), process or None

    async def _stream_audio(self, audio: AudioCapture | RemoteAudioSource) -> bytearray:
        """Stream audio chunks from the mic or the phone to the transcription server, returning the full buffer.

        When sending fails, the rest of the recording is still read up to its end marker (and kept), then the
        error is raised. Stopping at the failure would leave the unread chunks and the marker in the audio queue,
        and the next recording would start by reading them: every dictation after it would be the previous one.
        """
        buffer = bytearray()
        count = 0
        failure: Exception | None = None
        async for chunk in audio.chunks():
            buffer.extend(chunk)
            if failure is not None:
                continue
            try:
                await self._transcriber.send_audio_chunk(chunk)
                count += 1
            except Exception as exc:
                logger.exception("Error streaming audio")
                failure = exc
        # The microphone's recording summary says as much; a phone recording has none, so this is its only line.
        logger.log(
            logging.DEBUG if isinstance(audio, AudioCapture) else logging.INFO,
            "Streamed %d audio chunks to server",
            count,
        )
        if failure is not None:
            raise failure
        return buffer

    async def _abort_recording(
        self,
        audio: AudioCapture | RemoteAudioSource | None,
        stream_task: asyncio.Task | None,
        recording_stopped: bool,
    ) -> None:
        """After a failed cycle: end a stream that still runs and close the microphone it was reading.

        A cycle that failed before the stop must not leave its stream task reading the audio queue (it would
        take the next recording's chunks), nor the microphone open.
        """
        if audio is None:
            return
        try:
            if not recording_stopped:
                audio.stop()
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
            if stream_task is not None:
                await asyncio.gather(stream_task, return_exceptions=True)
        except Exception:
            logger.exception("Could not end the failed recording cleanly")
