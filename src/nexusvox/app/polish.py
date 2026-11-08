"""Text that goes in without a dictation: the paste key and Polish on the selected text."""

from __future__ import annotations

import asyncio
import functools
import time

from ..dictation.clipboard import ClipboardError
from ..dictation.feedback import beep_error
from ..dictation.injector import foreground_window, inject_text
from ..dictation.overlay import ERROR
from ..dictation.polish import DEFAULT_INSTRUCTION, polish, read_selection
from ..dictation.polish_presets import resolve_instruction, unexpected_script
from ..extensions import ActiveApp, ExtensionRegistry, loaded_extensions
from ..llm import LlmError, config_for, reload_unless_loaded
from ..system.logsetup import Text
from .common import logger
from .records import _Recording


class SelectionPolish:
    """The paste-key and Polish part of `NexusVoxApp`."""

    async def _paste_last_text(self) -> None:
        """Paste what the last dictation pasted, once more; the error beep when there is none yet."""
        if not self._last_typed:
            logger.info("Paste last: nothing pasted yet in this session")
            self._cue(beep_error, ERROR)
            return
        logger.info("Paste last: %s", Text(self._last_typed))
        rewritten = self._last_typed_rewritten  # model text reaching a field must not be learned as a user fix
        if rewritten:
            self._edit_watcher.pause()
        pasted = False
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, inject_text, self._last_typed, self._config.injection_delay_ms
            )
            pasted = True
        except ClipboardError as exc:
            logger.warning("Paste last: could not paste (%s)", exc)
            self._cue(beep_error, ERROR)
        finally:
            if rewritten:
                (self._edit_watcher.cancel if pasted else self._edit_watcher.resume)()

    async def _polish_selection(self, instruction: str, recording: _Recording | None) -> None:
        """Rewrite the selected text by *instruction* through the [llm] model and paste it over the selection.

        Nothing selected, or the model unreachable or failing: the error beep, and the
        selection stays as it was. When the focus moved to another window while the model
        worked, nothing is pasted (error beep); the paste key still pastes the reply.
        """
        loop = asyncio.get_running_loop()
        try:
            selection = await loop.run_in_executor(
                None, read_selection, self._config.injection_delay_ms, self._config.polish.in_terminals
            )
        except ClipboardError as exc:
            logger.warning("Polish: could not copy the selection (%s)", exc)
            self._cue(beep_error, ERROR)
            return
        if not selection.strip():
            logger.info("Polish: nothing selected")
            self._cue(beep_error, ERROR)
            return
        spoken = instruction
        window = foreground_window()  # before any hook runs: the paste checks the focus did not move meanwhile
        registry = loaded_extensions()
        deadline = ExtensionRegistry.selection_deadline()  # one budget for both polish hooks
        active_app = self._active_app() if registry is not None and registry.extensions else None
        if registry is not None and registry.extensions:
            handled = await loop.run_in_executor(
                None, functools.partial(registry.selection_command, selection, spoken, active_app, deadline=deadline)
            )
            if handled is not None:
                await self._paste_over_selection(
                    handled,
                    selection,
                    window,
                    "extension",
                    spoken,
                    spoken,
                    recording,
                    status="extension",
                    model_ms=None,
                )
                return
        presets = self._db.get_polish_presets()
        script = unexpected_script(spoken, presets)
        if script:
            # Not the text itself: a dictated instruction stays out of the log.
            logger.warning(
                "Polish: the instruction came back in %s script, not used (%d characters)", script, len(spoken)
            )
            self._cue(beep_error, ERROR)
            return
        instruction, preset = resolve_instruction(spoken, presets)
        if preset:
            logger.info("Polish: preset %r", preset)
        if registry is not None and registry.has_expand_instruction_hook():
            # An empty instruction (a tap, no default preset) is what the model would get as the default text.
            expanded = await loop.run_in_executor(
                None,
                functools.partial(
                    registry.expand_instruction,
                    instruction.strip() or DEFAULT_INSTRUCTION,
                    active_app,
                    deadline=deadline,
                ),
            )
            if expanded != (instruction.strip() or DEFAULT_INSTRUCTION):
                instruction = expanded
        detail = preset or spoken or "default"
        # The model this request goes to, read now: settings apply live, so a switch while it runs must not
        # credit the old model's time to the new one.
        llm = config_for(self._config.llm, "polish")
        asked = (llm.model, llm.url)
        started = time.perf_counter()  # not monotonic(): its tick is ~15 ms on Windows
        try:
            rewritten = await loop.run_in_executor(None, polish, selection, instruction, llm)
        except LlmError as exc:
            logger.warning("Polish: model at %s failed (%s); the selection stays as it was", llm.url, exc)
            self._cue(beep_error, ERROR)
            if exc.timed_out:
                # Not loaded (Ollama restarted or unloaded it): a short request cannot load it, so load it for the
                # next one. A model that is loaded and only slow is left alone.
                await loop.run_in_executor(None, reload_unless_loaded, llm)
            await self._save_polish(
                recording,
                "polish",
                detail,
                spoken,
                ok=False,
                status="timeout" if exc.timed_out else "failed",
                model_ms=round((time.perf_counter() - started) * 1000),
                llm=asked,
            )
            return
        model_ms = round((time.perf_counter() - started) * 1000)
        if registry is not None and registry.has_polish_result_hook():
            rewritten = await loop.run_in_executor(
                None, self._extend_polish_result, registry, selection, rewritten, active_app, llm.model
            )
        await self._paste_over_selection(
            rewritten,
            selection,
            window,
            "polish",
            detail,
            spoken,
            recording,
            status="applied",
            model_ms=model_ms,
            llm=asked,
        )

    @staticmethod
    def _extend_polish_result(
        registry: ExtensionRegistry, selection: str, rewritten: str, active_app: ActiveApp, model: str
    ) -> str:
        """*rewritten* after the extensions' `polish_result` hooks; they see the bare answer, not the whitespace
        `polish()` put back around it from *selection*, and that whitespace (a selected line's newline) is
        restored around what they return. The budget starts here, when the job runs, not when it was queued."""
        core = selection.strip()
        lead = selection[: selection.index(core)]
        trail = selection[len(lead) + len(core) :]
        if not (rewritten.startswith(lead) and rewritten.endswith(trail) and len(rewritten) >= len(lead) + len(trail)):
            return registry.polish_result(rewritten, active_app, model)
        bare = rewritten[len(lead) : len(rewritten) - len(trail)]
        return lead + registry.polish_result(bare, active_app, model) + trail

    async def _paste_over_selection(
        self,
        rewritten: str,
        selection: str,
        window: int,
        kind: str,
        detail: str,
        spoken: str,
        recording: _Recording | None,
        *,
        status: str,
        model_ms: int | None,
        llm: tuple[str, str] | None = None,
    ) -> None:
        """Paste *rewritten* over the still-selected text, unless the focus left *window*; save the outcome.

        Nothing pasted (the focus moved, the clipboard refused) is saved as ``failed``, however the reply came.
        *llm* is the (model, server URL) that was asked; None when no model was (an extension).
        """
        self._last_typed = rewritten
        self._last_typed_rewritten = True
        pasted = False
        # An empty reply or one equal to the selection changes nothing, so a running edit watch has no reason to end.
        changed = bool(rewritten) and rewritten != selection
        if foreground_window() != window:
            logger.info("Polish: the focus moved to another window, not pasting (the paste key pastes it)")
            self._cue(beep_error, ERROR)
        else:
            # The selection is still selected, so pasting replaces it; Ctrl+Z in the app undoes it.
            # The reads of a running edit watch stop before the paste, so the rewrite is never taken for the
            # user's fix; once it is in, the watch ends on what it read before.
            if changed:
                self._edit_watcher.pause()
            try:
                await asyncio.get_running_loop().run_in_executor(None, inject_text, rewritten, 0)
                pasted = True
            except ClipboardError as exc:
                logger.warning("Polish: could not paste the reply (%s); the paste key pastes it", exc)
                self._cue(beep_error, ERROR)
            finally:
                if changed:
                    (self._edit_watcher.cancel if pasted else self._edit_watcher.resume)()
        if pasted:
            logger.info("Polish: %s reply pasted%s", kind, "" if model_ms is None else f" (model took {model_ms} ms)")
        await self._save_polish(
            recording,
            kind,
            detail,
            spoken,
            ok=pasted,
            status=status if pasted else "failed",
            model_ms=model_ms,
            llm=llm,
        )

    async def _save_polish(
        self,
        recording: _Recording | None,
        kind: str,
        detail: str,
        spoken: str,
        *,
        ok: bool,
        status: str,
        model_ms: int | None,
        llm: tuple[str, str] | None = None,
    ) -> None:
        """Save a polish (or extension) attempt, off the event loop, as ``[kind] detail`` or ``[kind failed] detail``.

        A failed attempt gets its own label so the history does not show it as a polish that happened.
        *llm* is the (model, server URL) the request went to, taken when it was sent; None stores no model.
        """
        label = f"[{kind}{'' if ok else ' failed'}] {detail}"
        save = functools.partial(
            self._save_command,
            recording,
            label,
            spoken,
            polish_status=status,
            polish_ms=model_ms,
            llm_model=llm[0] if llm else None,
            llm_url=llm[1] if llm else None,
        )
        await asyncio.get_running_loop().run_in_executor(None, save)
