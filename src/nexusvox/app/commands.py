"""Dictations that do something else: the extensions' hooks, the assistant and the nexus commands."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Iterator

from ..dictation.assistant import parse_assistant_command, send_to_assistant
from ..dictation.feedback import beep_error, beep_flag
from ..dictation.os_commands import WINDOW_ACTIONS, NexusCommand, execute_nexus_command
from ..dictation.overlay import CONFIRMED, ERROR
from ..extensions import HOOK_TIMEOUT_S, ActiveApp, loaded_extensions
from ..system.logsetup import Text
from .common import logger


class CommandHandling:
    """The command part of `NexusVoxApp`."""

    _active_app_failed = False

    @classmethod
    def _active_app(cls) -> ActiveApp:
        try:
            from ..system.window_manager import foreground_app  # lazy, like the other window_manager uses here

            process, title = foreground_app()
        except Exception:
            # Every dictation asks; one traceback is enough when it keeps failing.
            level = logging.DEBUG if cls._active_app_failed else logging.ERROR
            logger.log(level, "Could not read the active app", exc_info=True)
            cls._active_app_failed = True
            return ActiveApp()
        return ActiveApp(process, title)

    # What is left of this dictation's `HOOK_TIMEOUT_S` for the extensions' hooks: `on_command` uses some of it
    # before the pipeline runs, `on_dictation` gets the rest. Reset for every dictation (one cycle at a time).
    _hook_budget_s: float = HOOK_TIMEOUT_S

    @contextlib.contextmanager
    def _charged(self) -> Iterator[None]:
        """Charge the time of the block to this dictation's hook budget."""
        started = time.monotonic()
        try:
            yield
        finally:
            self._hook_budget_s = max(0.0, self._hook_budget_s - (time.monotonic() - started))

    def _protect_dictation(self, text: str, *, remote: bool = False) -> tuple[str, Callable[[str], str | None]] | None:
        """The extensions' `protect` on a dictation, phone ones included (runs in the executor with the pipeline).

        Returns `(protected text, restore)` or None. `restore` draws on what is left of the hook budget when the
        pipeline calls it, not on the time cleanup and translation took in between.
        """
        registry = loaded_extensions()  # None while the packages still load: no protection, never wait
        if registry is None or not registry.has_protect_hook():
            return None  # no thread, no window lookup: an extension without the hook costs a dictation nothing
        with self._charged():
            answer = registry.protect(text, ActiveApp() if remote else self._active_app(), budget_s=self._hook_budget_s)
        if answer is None:
            return None
        protected, restore = answer

        def charged_restore(restoring: str) -> str | None:
            with self._charged():
                return restore(restoring, budget_s=self._hook_budget_s)

        return protected, charged_restore

    def _extend_dictation(self, text: str) -> str:
        """The extensions' say on a dictation about to be typed (runs in the executor with the pipeline)."""
        registry = loaded_extensions()  # None while the packages still load: type without them, never wait
        if registry is None or not registry.extensions:
            return text
        return registry.dictation(text, self._active_app(), budget_s=self._hook_budget_s)

    def _flag_last_transcription(self) -> None:
        """ "nexus flag" (``[experimental].flag_command``): flags the most recent transcription for review."""
        last_tid = self._db.get_last_transcription_id()
        if last_tid is None:
            logger.info("Nexus flag: no transcription to flag")
            return
        self._db.flag_transcription(last_tid)
        logger.info("Nexus flag: flagged transcription id=%d", last_tid)
        self._cue(beep_flag, CONFIRMED)

    def _extension_command(self, raw_text: str) -> tuple[str, bool] | None:
        """An extension's `on_command` answer for *raw_text* (runs in the executor), or None: nobody took it."""
        registry = loaded_extensions()  # None while the packages still load: no extension command, never wait
        if registry is None or not registry.has_command_hook():
            return None  # no thread, no window lookup: an extension without the hook costs a dictation nothing
        try:
            with self._charged():
                return registry.command(raw_text, self._active_app(), budget_s=self._hook_budget_s)
        except Exception:
            # A broken extension step must never lose the dictation (process_text guards on_dictation the same way).
            logger.exception("The extension command step failed; handling the dictation without it")
            return None

    async def _run_command(self, raw_text: str, nexus_cmd: NexusCommand | None) -> str | None:
        """Run the command *raw_text* is, if any. Returns the label to save it under, or None to type it.

        Checked in order: an extension's `on_command` (it consumes the dictation), "nexus assistant <text>"
        (when enabled; falls through to typing if nothing answers), nexus window commands (when enabled).
        """
        loop = asyncio.get_running_loop()
        handled = await loop.run_in_executor(None, self._extension_command, raw_text)
        if handled is not None:
            label, opened = handled
            if not opened:
                self._cue(beep_error, ERROR)
            return label

        assistant_text = parse_assistant_command(raw_text) if self._config.assistant.enabled else None
        if assistant_text is not None:
            try:
                reply = await loop.run_in_executor(None, send_to_assistant, assistant_text, self._config.assistant)
            except OSError as exc:
                logger.warning("Assistant not reachable (%s); typing the text instead", exc)
                self._cue(beep_error, ERROR)
            else:
                if reply.startswith("error:"):
                    logger.warning("Assistant refused the command: %s", Text(reply))
                    self._cue(beep_error, ERROR)
                return f"[assistant] {assistant_text}"

        if (
            nexus_cmd is not None
            and nexus_cmd.action in WINDOW_ACTIONS
            and self._config.experimental.os_commands
            and self._config.os_commands.enabled
        ):
            await loop.run_in_executor(None, execute_nexus_command, nexus_cmd, self._config.os_commands)
            logger.info("Nexus command: %s %s", nexus_cmd.action, nexus_cmd.app_name)
            return f"[nexus] {nexus_cmd.action} {nexus_cmd.app_name}"
        return None
