"""Optional extensions, discovered through Python entry points.

An installed package registers an `Extension` under the `nexusvox.extensions` entry-point group:

    [project.entry-points."nexusvox.extensions"]
    my_extension = "my_package:MyExtension"

The entry point may name an `Extension` subclass, an instance, or a zero-argument function returning one.
NexusVox loads every extension once at start. An extension that cannot be imported, targets an
another `API_VERSION`, is built wrongly or raises in a hook is logged and skipped; it never stops the app.
With no extension installed nothing changes. The hooks are documented in docs/EXTENSIONS.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .system.logsetup import Text
from .transcription.transcriber import Word

if TYPE_CHECKING:
    from flask import Blueprint

    from .storage.models import FileTranscription

logger = logging.getLogger(__name__)

# Bumped on a change that breaks existing extensions; an additive change keeps it.
API_VERSION = 1
ENTRY_POINT_GROUP = "nexusvox.extensions"
# Developer switch: exactly "1" starts the app without any entry-point extension (docs/EXTENSIONS.md).
DISABLE_ENV_VAR = "NEXUSVOX_NO_EXTENSIONS"
# How long a dictation or selection hook may take before its result is dropped and the text stays as it was.
HOOK_TIMEOUT_S = 3.0
# A selection command may wait for a local language model; nobody types meanwhile and the core polish allows longer.
SELECTION_COMMAND_TIMEOUT_S = 15.0
# Extra time per text for `display_text`, which is called for every cue of a subtitle export.
DISPLAY_TEXT_S = 0.01

_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
_SUFFIX_PATTERN = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._-]*")
_MIMETYPE_PATTERN = re.compile(r"[\w.+-]+/[\w.+-]+(; ?charset=[\w-]+)?")


@dataclass(frozen=True)
class FileTranscript:
    """A stored file transcription, as the hooks receive it. Read-only: the extension keeps its own results."""

    id: int
    text: str  # as transcribed (with `Speaker N:` paragraphs when speaker detection ran)
    language: str
    duration_ms: int
    original_filename: str
    model: str | None = None
    created_at: str | None = None  # ISO 8601
    cleaned_text: str | None = None  # the LLM-cleaned version, when text cleanup ran and changed something
    cleanup_level: str | None = None
    words: tuple[Word, ...] = ()  # per-word timing in seconds; empty when the engine reported none
    # "upload" for a file, "meeting" for a two-track recording (speaker 1 is the microphone, 2 and up the system
    # audio). Added without an API_VERSION bump: a hook that ignores it keeps working.
    kind: str = "upload"
    # Names that replace "Speaker N" in `text`, by speaker number (a meeting names its own voice). Also additive.
    speaker_names: dict[int, str] = field(default_factory=dict)
    # The name the user gave the row, if any; `original_filename` stays the file or meeting folder name. Also additive.
    title: str | None = None

    @property
    def best_text(self) -> str:
        """The cleaned text when there is one, else the transcript as transcribed."""
        return self.cleaned_text or self.text


def _speaker_names_from_json(raw: str | None) -> dict[int, str]:
    try:
        return {int(number): str(name) for number, name in json.loads(raw or "{}").items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def file_transcript_from_record(record: FileTranscription) -> FileTranscript:
    """The hook view of a `file_transcriptions` row."""
    words: list[Word] = []
    if record.words:
        try:
            words = [
                Word(w["word"], float(w["start"]), float(w["end"]), w.get("speaker")) for w in json.loads(record.words)
            ]
        except (ValueError, KeyError, TypeError):
            logger.warning("Word timings of file transcription %s are unreadable; passing none on", record.id)
    return FileTranscript(
        id=record.id,
        text=record.text,
        language=record.language,
        duration_ms=record.duration_ms,
        original_filename=record.original_filename,
        model=record.model,
        created_at=record.created_at.isoformat() if record.created_at else None,
        cleaned_text=record.cleaned_text,
        cleanup_level=record.cleanup_level,
        words=tuple(words),
        kind=record.kind or "upload",
        speaker_names=_speaker_names_from_json(record.speaker_names),
        title=record.title,
    )


@dataclass(frozen=True)
class ActiveApp:
    """The app that has the focus: where a dictation is about to be typed, or whose selection is rewritten."""

    process: str = ""  # executable file name, e.g. `Code.exe`; empty when it could not be read
    title: str = ""  # the window title; empty when it could not be read


@dataclass(frozen=True)
class DashboardPanel:
    """An extra dashboard tab.

    `static_dir` is served at `/ext/<id>/`; the browser loads `/ext/<id>/<script>`, which registers a render
    function with `NexusVoxExt.registerPanel("<id>", container => ...)`. `blueprint` (optional) is mounted at
    `/api/ext/<id>/`, so its routes are the panel's backend. `recording_script` (optional, also in `static_dir`)
    is loaded when the dashboard's Recordings tab opens, so it can add tabs to the recording detail pane with
    `NexusVoxExt.registerRecordingTab`; keep it light, it runs whether or not the panel's own tab is ever opened.
    """

    id: str  # lower-case letters, digits, `-` and `_`; unique across all extensions
    title: str
    static_dir: Path
    script: str = "panel.js"
    blueprint: Blueprint | None = None
    recording_script: str | None = None


@dataclass(frozen=True)
class ExportTarget:
    """A way to export a stored file transcription, offered in the Export menu of the Recordings tab."""

    id: str  # same rules as `DashboardPanel.id`
    label: str  # shown on the download link
    extension: str  # file name suffix with the dot, e.g. `.docx`
    mimetype: str
    export: Callable[[FileTranscript], bytes | str]
    # Optional, per upload: True/None offers the link; any other falsy answer (False, 0, ...) greys it out;
    # a string greys it out with that text as the tooltip. A dict shows a state instead: `{"pending": "<label>"}`
    # (label with a spinner; the dashboard keeps refreshing until it is gone) or `{"error": "<reason>", "retry":
    # "<upload action id>"}` (the reason plus a Retry button that runs that action). Called while the Upload list
    # is built, so keep it cheap.
    available: Callable[[int], bool | str | dict | None] | None = None


@dataclass(frozen=True)
class UploadAction:
    """A button on an upload's result card.

    Either `endpoint` (the browser POSTs to it; `{id}` stands for the upload id; it answers JSON, `error` or
    `note`/`message` is shown) or `panel` (the id of one of the extension's own panels: its script registers
    `NexusVoxExt.registerUploadAction("<id>", (upload, button) => ...)`).
    """

    id: str  # same rules as `DashboardPanel.id`
    label: str
    endpoint: str = ""  # an absolute path on this server, e.g. `/api/ext/my-tab/summarise/{id}`
    panel: str = ""
    # Optional, per upload, with the same answers as `ExportTarget.available`: a falsy answer or a string greys the
    # button out (the string is its tooltip); a dict shows a pending or failed state. Called while the Upload list
    # is built, so keep it cheap.
    available: Callable[[int], bool | str | dict | None] | None = None


class Extension:
    """Base class of an extension. Override what you need; every hook has a no-op default."""

    name: str = ""  # required: shown in logs and results, must be unique
    version: str = ""
    # Required, and deliberately without a default: a literal such as `api_version = 1`, the API this extension
    # was written for. A default of API_VERSION would be inherited as the new value after a bump.
    api_version: int | None = None

    def on_file_transcribed(self, transcript: FileTranscript) -> dict[str, Any] | None:
        """Post-process a file upload that was transcribed and stored.

        Runs on the upload's worker thread, after the save, so a slow hook delays the end of the job but
        cannot lose the transcript. Return a JSON-serialisable dict to add it to the job's result under
        `extensions[<name>]`; a `note` string in it is also shown as a note. Return None to add nothing.
        """
        return None

    def on_command(self, text: str, app: ActiveApp) -> tuple[str, bool] | None:
        """Consume a dictation as a command (a spoken action) instead of letting it be typed.

        *text* is the transcript as spoken (nothing of the text pipeline has run). Return None when it is not
        this extension's command. Otherwise return `(label, opened)`: the core saves the dictation under *label*
        (e.g. `[nexus] open <target>`), types nothing, and plays the error cue when *opened* is False (the command
        was recognised but failed). Called before the core's own commands (but after "nexus flag", which the core
        handles first when `[experimental].flag_command` is on), so an extension can take over a phrase; the first
        extension that answers wins. Several extensions run in entry-point order and, together with `on_dictation`,
        share one `HOOK_TIMEOUT_S` per dictation. A hook that is too slow or raises is skipped and the dictation is
        typed, so do the action itself quickly (or on a thread of your own). Never called for a phone (remote-mic)
        dictation.
        """
        return None

    def protect(self, text: str, app: ActiveApp) -> tuple[str, Callable[[str], str | None]] | None:
        """Shield parts of a dictation from the text pipeline, and put them back at its end.

        Called with the transcript after the correction dictionary and before voice commands, cleanup and
        translation, for a dictation typed on this PC and for a phone (remote-mic) one; for the phone *app* is an
        empty `ActiveApp`. Return None to leave the text alone. Otherwise return `(protected, restore)`: the
        pipeline works on *protected* (e.g. a trigger phrase swapped for a placeholder that cleanup and the
        translator leave alone), and at the end calls `restore(text)` on the text to type and on the text to save.
        `restore` returns the final text, or None when it cannot (e.g. the translator dropped a placeholder); the
        core then runs the dictation again without any `protect`. A hook that raises, is too slow or answers
        anything else is skipped and the dictation goes through unprotected. Several extensions run in entry-point
        order, each seeing the previous one's *protected* text; their `restore` calls run in reverse order. The
        `protect` and `restore` calls share the dictation's `HOOK_TIMEOUT_S` with `on_command` and `on_dictation`.
        """
        return None

    def on_dictation(self, text: str, app: ActiveApp) -> str | None:
        """Change a dictation just before it is typed into *app*.

        *text* is what would be typed (dictionary, voice commands, cleanup and translation are done).
        Return the text to type instead, or None to leave it. Blocking is fine (it runs off the UI and
        hotkey threads) but a hook that takes longer than `HOOK_TIMEOUT_S` is ignored. Several extensions run
        in the order of their entry-point names, each seeing the previous one's result.
        """
        return None

    def on_selection_command(self, selection: str, command: str, app: ActiveApp) -> str | None:
        """Handle a spoken command on the selected text (the polish key, see docs/EXTENSIONS.md).

        Return the replacement for *selection*, or None when this extension does not know *command*; the
        first extension that returns text wins and the core pastes it over the selection. When none does,
        the built-in language-model polish runs as before.
        """
        return None

    def expand_instruction(self, instruction: str, app: ActiveApp) -> str | None:
        """Rewrite the polish instruction just before it goes to the language model.

        *instruction* is what the model would be told: the spoken words, or the preset's instruction once a preset
        name is resolved (`polish_presets.resolve_instruction`). Return the instruction to send instead, or None
        to leave it. Runs only when no `on_selection_command` extension handled the command. Several extensions
        run in entry-point order, each seeing the previous result; one that raises, answers with something that is
        not a string or is too slow is logged and skipped, and the polish runs with the instruction as it was.
        """
        return None

    def display_text(self, transcript: FileTranscript, text: str) -> str | None:
        """Change a transcript text the core shows or exports for a stored upload or meeting.

        *text* is the upload's transcript, its cleaned version, or a subtitle cue (`Speaker 1: ...`); it is
        called once per text, for the Recordings tab and the built-in TXT, SRT and VTT exports. Return the text to
        show instead (e.g. with `Speaker 1` replaced by a name), or None to leave it. The stored transcript
        never changes. Several extensions run in entry-point order, each seeing the previous result; one that
        raises, answers with something that is not a string, or is too slow (`HOOK_TIMEOUT_S`) is skipped.
        """
        return None

    def dashboard_panels(self) -> list[DashboardPanel]:
        """Extra dashboard tabs."""
        return []

    def export_targets(self) -> list[ExportTarget]:
        """Extra ways to export a stored file transcription."""
        return []

    def upload_actions(self) -> list[UploadAction]:
        """Extra buttons on an upload's result card."""
        return []


def _start_call(fn: Callable[..., Any], *args: Any) -> tuple[threading.Thread, list[tuple[bool, Any]]]:
    """Start *fn* on a helper thread; the list gets `(True, result)` or `(False, exception)` when it ends.

    Anything the hook raises, `SystemExit` included, comes back as an ordinary exception: a misbehaving
    extension must never end the caller's thread or the app.
    """
    outcome: list[tuple[bool, Any]] = []

    def run() -> None:
        try:
            outcome.append((True, fn(*args)))
        except Exception as exc:
            outcome.append((False, exc))
        except BaseException as exc:  # SystemExit, KeyboardInterrupt, ...
            outcome.append((False, RuntimeError(f"{type(exc).__name__}: {exc}")))

    worker = threading.Thread(target=run, name="extension-hook", daemon=True)
    worker.start()
    return worker, outcome


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


def _is_local_path(value: object) -> bool:
    """True for an absolute path on this server: no scheme or host, no backslash (browsers read it as `/`)."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return False
    parts = urlsplit(value)
    return not parts.scheme and not parts.netloc


def _problem_with_panel(panel: DashboardPanel) -> str:
    """Why a panel cannot be served, or an empty string."""
    if not isinstance(panel.title, str) or not panel.title:
        return "it has no title"
    folder = Path(panel.static_dir)
    if not folder.is_absolute():
        return f"static_dir {str(folder)!r} is not absolute (use Path(__file__).parent / ...)"
    if not folder.is_dir():
        return f"static_dir {str(folder)!r} is not a folder"
    if panel.recording_script is not None and (
        not isinstance(panel.recording_script, str) or not panel.recording_script
    ):
        return "recording_script must be a file name in static_dir"
    return ""


def _describe_panel(panel: DashboardPanel) -> dict:
    """A panel as `GET /api/extensions` lists it; `recording_script` only when the panel has one."""
    described = {"id": panel.id, "title": panel.title, "script": f"/ext/{panel.id}/{panel.script}"}
    if panel.recording_script:
        described["recording_script"] = f"/ext/{panel.id}/{panel.recording_script}"
    return described


def _problem_with_target(target: ExportTarget) -> str:
    """Why an export target cannot be offered, or an empty string."""
    if not isinstance(target.label, str) or not target.label:
        return "it has no label"
    if not isinstance(target.extension, str) or _SUFFIX_PATTERN.fullmatch(target.extension) is None:
        return f"extension {target.extension!r} is not a file name suffix such as '.docx'"
    if not isinstance(target.mimetype, str) or _MIMETYPE_PATTERN.fullmatch(target.mimetype) is None:
        return f"mimetype {target.mimetype!r} is not a media type such as 'text/csv'"
    if not callable(target.export):
        return "export is not callable"
    if target.available is not None and not callable(target.available):
        return "available is not callable"
    return ""


def _availability_state(answer: dict) -> str | dict:
    """A pending or failed state from an `available` check, cleaned for the dashboard; anything else greys out."""
    if isinstance(answer.get("pending"), str):
        return {"pending": answer["pending"] or "Working"}
    if isinstance(answer.get("error"), str):
        state = {"error": answer["error"] or "Failed"}
        if isinstance(answer.get("retry"), str) and answer["retry"]:
            state["retry"] = answer["retry"]
        return state
    return "Not available"


def _problem_with_action(action: UploadAction, own_panels: set[str]) -> str:
    """Why an upload action cannot be offered, or an empty string."""
    if not isinstance(action.label, str) or not action.label:
        return "it has no label"
    if bool(action.endpoint) == bool(action.panel):
        return "it needs exactly one of endpoint and panel"
    if action.endpoint and not _is_local_path(action.endpoint):
        return f"endpoint {action.endpoint!r} is not an absolute path on this server"
    if action.panel and action.panel not in own_panels:
        return f"panel {action.panel!r} is not one of the extension's own panels"
    if action.available is not None and not callable(action.available):
        return "available is not callable"
    return ""


@dataclass
class ExtensionRegistry:
    """The loaded extensions and the dispatch of the hooks."""

    extensions: list[Extension] = field(default_factory=list)
    _panels: dict[str, DashboardPanel] = field(default_factory=dict)
    _targets: dict[str, ExportTarget] = field(default_factory=dict)
    _actions: dict[str, UploadAction] = field(default_factory=dict)
    _target_owner: dict[str, str] = field(default_factory=dict)  # id -> the name of the extension that registered it
    _action_owner: dict[str, str] = field(default_factory=dict)
    # The last pending state each check answered, `{(kind, item id, upload id): state}`: a check that is skipped or too
    # slow on a later refresh keeps showing it, so a running job never looks finished (and the polling never stops).
    _last_pending: dict[tuple[str, str, int], dict] = field(default_factory=dict)
    _stuck: dict[tuple[str, str], threading.Thread] = field(default_factory=dict)
    _stuck_lock: threading.Lock = field(default_factory=threading.Lock)  # the dashboard calls hooks from many threads

    # ---- Loading -----------------------------------------------------------

    def load(self, entry_point_list: list | None = None) -> None:
        """Load every extension of the entry-point group; skip, and log, any that fails.

        `entry_point_list` replaces the discovery (tests pass fakes). With `NEXUSVOX_NO_EXTENSIONS=1`
        nothing is loaded.
        """
        if os.environ.get(DISABLE_ENV_VAR) == "1":
            logger.info("Extensions are disabled by %s=1; none loaded", DISABLE_ENV_VAR)
            return
        try:
            found = (
                list(entry_point_list) if entry_point_list is not None else list(entry_points(group=ENTRY_POINT_GROUP))
            )
        except Exception:
            logger.exception("Could not look up the %s entry points; no extensions loaded", ENTRY_POINT_GROUP)
            return
        for point in sorted(found, key=lambda p: p.name):
            try:
                self.add(point.load(), source=point.name)
            except Exception:
                logger.exception("Extension %r could not be loaded and is skipped", point.name)

    def add(self, loaded: object, *, source: str = "") -> bool:
        """Register an extension (class, instance or factory); False, with a log line, when it is unusable."""
        try:
            extension = loaded if isinstance(loaded, Extension) else loaded() if callable(loaded) else loaded
            if not isinstance(extension, Extension):
                logger.error("Extension %r is not a nexusvox.extensions.Extension; skipped", source or loaded)
                return False
            label = extension.name or source
            if not extension.name:
                logger.error("Extension %r has no name; skipped", source or extension)
                return False
            if extension.api_version != API_VERSION:
                logger.error(
                    "Extension %r targets extension API %s (None: it declares none), this NexusVox provides %s; "
                    "skipped",
                    label,
                    extension.api_version,
                    API_VERSION,
                )
                return False
            if any(e.name == extension.name for e in self.extensions):
                logger.error("Extension name %r is already taken; skipped", label)
                return False
            panels = self._claim(
                extension.dashboard_panels(), self._panels, DashboardPanel, label, "panel", _problem_with_panel
            )
            targets = self._claim(
                extension.export_targets(), self._targets, ExportTarget, label, "export target", _problem_with_target
            )
            own_panels = {p.id for p in panels}
            actions = self._claim(
                extension.upload_actions(),
                self._actions,
                UploadAction,
                label,
                "upload action",
                lambda action: _problem_with_action(action, own_panels),
            )
        except Exception:
            logger.exception("Extension %r failed while registering and is skipped", source or loaded)
            return False
        self._panels.update({p.id: p for p in panels})
        self._targets.update({t.id: t for t in targets})
        self._actions.update({a.id: a for a in actions})
        self._target_owner.update({t.id: extension.name for t in targets})
        self._action_owner.update({a.id: extension.name for a in actions})
        self.extensions.append(extension)
        logger.info("Loaded extension %s %s", extension.name, extension.version)
        return True

    @staticmethod
    def _claim(items: list, taken: dict, kind: type, extension: str, what: str, problem: Callable) -> list:
        """The items of an extension that are well formed and whose id is free; the others are logged."""
        claimed: list = []
        for item in items:
            if not isinstance(item, kind) or not _valid_id(item.id):
                logger.error("Extension %r offers a malformed %s %r; skipped", extension, what, item)
            elif reason := problem(item):
                logger.error("Extension %r: %s %r is skipped: %s", extension, what, item.id, reason)
            elif item.id in taken or any(c.id == item.id for c in claimed):
                logger.error("Extension %r: %s id %r is already taken; skipped", extension, what, item.id)
            else:
                claimed.append(item)
        return claimed

    def add_builtin_exports(self) -> None:
        """Register the export targets NexusVox ships (TXT, SRT, VTT) before the extensions, so their ids stay ours."""
        try:
            from .transcription.exports import builtin_export_targets  # imported here: exports imports this module

            self._targets.update({t.id: t for t in builtin_export_targets(self.display_texts)})
        except Exception:
            logger.exception("The built-in export targets could not be registered; continuing without them")

    # ---- Hook: file upload transcribed ---------------------------------------

    def file_transcribed(self, transcript: FileTranscript) -> tuple[dict[str, Any], list[str]]:
        """Run every extension's post-processing step: `({name: result}, notes)`.

        A hook that raises or returns something that is not a JSON-serialisable dict adds a note instead.
        """
        results: dict[str, Any] = {}
        notes: list[str] = []
        for extension in self.extensions:
            try:
                result = extension.on_file_transcribed(transcript)
                if result is None:
                    continue
                if not isinstance(result, dict):
                    raise TypeError(f"expected a dict, got {type(result).__name__}")
                json.dumps(result, sort_keys=True)  # what jsonify does: mixed key types would fail every poll
            except Exception as exc:
                logger.exception("Extension %s failed on file transcription %s", extension.name, transcript.id)
                notes.append(f"Extension {extension.name} failed: {exc}")
                continue
            results[extension.name] = result
            if isinstance(result.get("note"), str) and result["note"]:
                notes.append(result["note"])
        return results, notes

    # ---- Hooks: dictation and selection commands -----------------------------

    def has_command_hook(self) -> bool:
        """True when any loaded extension overrides `on_command`; with none, asking costs nothing."""
        return any(type(e).on_command is not Extension.on_command for e in self.extensions)

    def has_protect_hook(self) -> bool:
        """True when any loaded extension overrides `protect`; with none, asking costs nothing."""
        return any(type(e).protect is not Extension.protect for e in self.extensions)

    def protect(
        self, text: str, app: ActiveApp, *, budget_s: float | None = None
    ) -> tuple[str, Callable[..., str | None]] | None:
        """`(protected, restore)` from every extension's `protect` in load order, or None when none changed *text*.

        An extension that raises, is too slow or answers something other than a `(non-empty str, callable)` pair is
        logged and skipped; the text stays as the previous extension left it. Extensions that do not override
        `protect` are not called. *budget_s* is the time all of them may take together.

        `restore(text, budget_s=None)` runs the extensions' restores in reverse order, sharing one deadline
        (`HOOK_TIMEOUT_S` by default), and returns the restored text, or None when any of them raised, was too
        slow or did not return a string: a half-restored text must never be typed, the caller starts over without
        `protect`.
        """
        deadline = time.monotonic() + (HOOK_TIMEOUT_S if budget_s is None else budget_s)
        restores: list[tuple[Extension, Callable[[str], str | None]]] = []
        for extension in self.extensions:
            if type(extension).protect is Extension.protect:
                continue
            answer = self._guarded(
                (extension.name, "protect"),
                f"Extension {extension.name}",
                "protect",
                deadline,
                extension.protect,
                text,
                app,
            )
            if answer is None:
                continue
            if (
                isinstance(answer, tuple)
                and len(answer) == 2
                and isinstance(answer[0], str)
                and (answer[0] or not text)
                and callable(answer[1])
            ):
                text = answer[0]
                restores.append((extension, answer[1]))
                continue
            logger.error(
                "Extension %s answered protect with %s, not a (text, restore) pair; ignored",
                extension.name,
                type(answer).__name__,
            )
        if not restores:
            return None

        def restore(restored: str, budget_s: float | None = None) -> str | None:
            restore_deadline = time.monotonic() + (HOOK_TIMEOUT_S if budget_s is None else budget_s)
            for extension, fn in reversed(restores):
                answer = self._guarded(
                    (extension.name, "protect.restore"),
                    f"Extension {extension.name}",
                    "its restore",
                    restore_deadline,
                    fn,
                    restored,
                )
                if not isinstance(answer, str) or (not answer and restored):
                    if answer is not None:
                        logger.error(
                            "Extension %s answered its restore with %s, not text; the dictation starts over",
                            extension.name,
                            type(answer).__name__,
                        )
                    return None
                restored = answer
            return restored

        return text, restore

    def command(self, text: str, app: ActiveApp, *, budget_s: float | None = None) -> tuple[str, bool] | None:
        """`(label, opened)` from the first extension whose `on_command` takes *text*, else None.

        An extension that raises, is too slow or answers something other than a `(non-empty str, bool)` pair
        is logged and skipped; the next one is asked, and when none takes it the dictation goes on as usual (so
        a hook that timed out may already have acted: keep the action quick, see docs/EXTENSIONS.md). Extensions
        that do not override `on_command` are not called. *budget_s* is the time all of them may take together;
        the caller passes what is left of the dictation's `HOOK_TIMEOUT_S` on to `dictation`.
        """
        deadline = time.monotonic() + (HOOK_TIMEOUT_S if budget_s is None else budget_s)
        for extension in self.extensions:
            if type(extension).on_command is Extension.on_command:
                continue
            answer = self._guarded(
                (extension.name, "on_command"),
                f"Extension {extension.name}",
                "on_command",
                deadline,
                extension.on_command,
                text,
                app,
            )
            if answer is None:
                continue
            if (
                isinstance(answer, tuple)
                and len(answer) == 2
                and isinstance(answer[0], str)
                and answer[0].strip()
                and isinstance(answer[1], bool)
            ):
                logger.info("Extension %s handled the command %r", extension.name, Text(text))
                return answer[0], answer[1]
            logger.error(
                "Extension %s answered on_command with %s, not a (label, opened) pair; ignored",
                extension.name,
                type(answer).__name__,
            )
        return None

    def dictation(self, text: str, app: ActiveApp, *, budget_s: float | None = None) -> str:
        """*text* as every extension's `on_dictation` leaves it, in load order.

        An extension that raises, returns something that is not a string, or does not answer in time is
        skipped: the text stays as the previous step left it. The whole chain shares one `HOOK_TIMEOUT_S`, or the
        *budget_s* the caller says is left of it after `command`. With no extension this returns *text* itself.
        """
        deadline = time.monotonic() + (HOOK_TIMEOUT_S if budget_s is None else budget_s)
        for extension in self.extensions:
            result = self._text_hook(extension, "on_dictation", deadline, text, app)
            if result is not None:
                text = result
        return text

    def display_texts(
        self, transcript: FileTranscript, texts: list[str], *, deadline: float | None = None
    ) -> list[str]:
        """*texts* as every extension's `display_text` leaves them, in load order; always the same number.

        Each extension gets all texts in one helper thread and one budget (`deadline`, a `time.monotonic()`
        value; by default `HOOK_TIMEOUT_S` from now plus `DISPLAY_TEXT_S` per text, so a long subtitle file is not
        cut off half way). An extension that does not finish in time is skipped for all texts, never for some. A
        text whose call raises, answers something that is not a string, or answers empty for a non-empty text
        stays as it was; with no extension the texts come back unchanged.
        """
        out = list(texts)
        if not self.extensions or not out:
            return out
        if deadline is None:
            deadline = time.monotonic() + HOOK_TIMEOUT_S + DISPLAY_TEXT_S * len(out)
        for extension in self.extensions:
            current = list(out)
            answers = self._guarded(
                (extension.name, "display_text"),
                f"Extension {extension.name}",
                "display_text",
                deadline,
                self._display_all,
                extension,
                transcript,
                current,
            )
            if answers is not None:
                out = answers
        return out

    @staticmethod
    def _display_all(extension: Extension, transcript: FileTranscript, texts: list[str]) -> list[str]:
        """One extension's `display_text` over *texts*; a text it fails on keeps its wording."""
        out = []
        for text in texts:
            try:
                result = extension.display_text(transcript, text)
            except Exception as exc:
                logger.warning("Extension %s failed in display_text: %s", extension.name, exc)
                result = None
            if isinstance(result, str) and (result or not text):
                text = result
            elif result is not None and not isinstance(result, str):
                logger.error(
                    "Extension %s returned %s from display_text, not a string; ignored",
                    extension.name,
                    type(result).__name__,
                )
            out.append(text)
        return out

    def selection_command(self, selection: str, command: str, app: ActiveApp) -> str | None:
        """The replacement for *selection* from the first extension that handles *command*, else None."""
        deadline = time.monotonic() + SELECTION_COMMAND_TIMEOUT_S
        for extension in self.extensions:
            result = self._text_hook(extension, "on_selection_command", deadline, selection, command, app)
            if result is not None:
                logger.info("Extension %s handled the selection command %r", extension.name, Text(command))
                return result
        return None

    def expand_instruction(self, instruction: str, app: ActiveApp) -> str:
        """*instruction* as every extension's `expand_instruction` leaves it, in load order; unchanged with none."""
        deadline = time.monotonic() + SELECTION_COMMAND_TIMEOUT_S
        for extension in self.extensions:
            if type(extension).expand_instruction is Extension.expand_instruction:
                continue
            result = self._text_hook(extension, "expand_instruction", deadline, instruction, app)
            if result is not None:
                instruction = result
        return instruction

    def _text_hook(self, extension: Extension, hook: str, deadline: float, *args: Any) -> str | None:
        """What one extension's text hook answered, or None: nothing to say, or it failed (logged)."""
        value = self._guarded(
            (extension.name, hook), f"Extension {extension.name}", hook, deadline, getattr(extension, hook), *args
        )
        if value is not None and not isinstance(value, str):
            logger.error(
                "Extension %s returned %s from %s, not a string; ignored", extension.name, type(value).__name__, hook
            )
            return None
        return value

    def _guarded(self, key: tuple[str, str], who: str, hook: str, deadline: float, fn: Callable, *args: Any) -> Any:
        """`fn(*args)` on a helper thread, waiting until *deadline*; None when it failed or was too slow (logged).

        A call still running from an earlier timeout (same *key*) is not started again, so a hanging extension
        costs one thread, not one per dictation.
        """
        with self._stuck_lock:
            stuck = self._stuck.get(key)
            if stuck is not None and not stuck.is_alive():
                del self._stuck[key]
                stuck = None
        if stuck is not None:
            logger.warning("%s is still busy in %s; skipped", who, hook)
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.error("%s: no time left for %s; skipped", who, hook)
            return None
        worker, outcome = _start_call(fn, *args)
        worker.join(remaining)
        if not outcome:
            with self._stuck_lock:
                self._stuck.setdefault(key, worker)
            logger.error("%s did not answer %s in time; skipped", who, hook)
            return None
        ok, value = outcome[0]
        if not ok:
            logger.error("%s failed in %s; skipped", who, hook, exc_info=value)
            return None
        return value

    # ---- Hooks: dashboard panels and export targets --------------------------

    def panels(self) -> list[DashboardPanel]:
        return list(self._panels.values())

    def panel(self, panel_id: str) -> DashboardPanel | None:
        return self._panels.get(panel_id)

    def export_targets(self) -> list[ExportTarget]:
        return list(self._targets.values())

    def export_target(self, target_id: str) -> ExportTarget | None:
        return self._targets.get(target_id)

    def upload_actions(self) -> list[UploadAction]:
        return list(self._actions.values())

    def unavailable_exports(self, transcription_id: int, *, deadline: float | None = None) -> dict[str, str | dict]:
        """The export targets that cannot export this upload, `{target id: reason}`.

        A reason is a string, or a state dict: `{"pending": label}` / `{"error": reason, "retry": action id}`.

        A target without `available` is always offered; one whose check raises or is too slow is offered too, so
        the export itself reports what is wrong. *deadline* (`time.monotonic()`) bounds all checks of a list.
        """
        return self._unavailable(
            self._targets.values(), transcription_id, "Export target", deadline, self._target_owner
        )

    def unavailable_actions(self, transcription_id: int, *, deadline: float | None = None) -> dict[str, str | dict]:
        """The upload actions that are greyed out for this upload, `{action id: reason}` (same rules)."""
        return self._unavailable(
            self._actions.values(), transcription_id, "Upload action", deadline, self._action_owner
        )

    def _unavailable(
        self, items, transcription_id: int, what: str, deadline: float | None, owners: dict[str, str]
    ) -> dict[str, str | dict]:
        if deadline is None:
            deadline = time.monotonic() + HOOK_TIMEOUT_S
        unavailable: dict[str, str | dict] = {}
        for item in items:
            if item.available is None:
                continue
            who = f"{what} {item.id}"
            memo = (what, item.id, transcription_id)
            try:
                # Wrapped in a tuple, so "the check was skipped or failed" (None) differs from "it answered None".
                outcome = self._guarded(
                    (who, "available"),
                    who,
                    "its availability check",
                    deadline,
                    lambda tid, check=item.available: (check(tid),),
                    transcription_id,
                )
            except Exception:  # _guarded never raises; kept so one odd item cannot hide the others
                logger.exception("%s failed its availability check", who)
                continue
            if outcome is None:
                if memo in self._last_pending:
                    unavailable[item.id] = self._last_pending[memo]
                continue
            answer = outcome[0]
            self._last_pending.pop(memo, None)
            if isinstance(answer, dict):
                state = _availability_state(answer)
                if isinstance(state, dict) and "retry" in state:
                    owner = owners.get(item.id)
                    if state["retry"] not in self._action_owner or self._action_owner[state["retry"]] != owner:
                        logger.warning(
                            "%s answers retry=%r, which is not an upload action of its extension; Retry dropped",
                            who,
                            state["retry"],
                        )
                        del state["retry"]
                if isinstance(state, dict) and "pending" in state:
                    self._last_pending[memo] = state
                unavailable[item.id] = state
            elif isinstance(answer, str):
                unavailable[item.id] = answer or "Not available"
            elif answer is not None and not answer:
                unavailable[item.id] = "Not available"
        return unavailable

    def describe(self) -> dict:
        """What the dashboard needs to show the extensions' panels and export links."""
        return {
            "api_version": API_VERSION,
            "extensions": [{"name": e.name, "version": e.version} for e in self.extensions],
            "panels": [_describe_panel(p) for p in self._panels.values()],
            "exports": [
                {"id": t.id, "label": t.label, "extension": t.extension, "mimetype": t.mimetype}
                for t in self._targets.values()
            ],
            "upload_actions": [
                {"id": a.id, "label": a.label, "endpoint": a.endpoint, "panel": a.panel} for a in self._actions.values()
            ],
        }


_default = ExtensionRegistry()
_default_loaded = False  # loading has started
_default_ready = False  # loading has finished
_default_lock = threading.RLock()


def load_extensions() -> ExtensionRegistry:
    """The process-wide registry, filled from the installed entry points on the first call."""
    global _default_loaded, _default_ready
    with _default_lock:  # reentrant: an extension calling this while it loads gets the registry as filled so far
        if not _default_loaded:
            _default_loaded = True
            _default.add_builtin_exports()
            _default.load()
            _default_ready = True
    return _default


def loaded_extensions() -> ExtensionRegistry | None:
    """The registry once the entry points are loaded, else None. Never waits: the dictation path uses it so a
    heavy package still loading cannot delay a dictation (which is then typed without extensions)."""
    return _default if _default_ready else None
