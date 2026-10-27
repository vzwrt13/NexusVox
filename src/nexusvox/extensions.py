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
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .transcriber import Word

if TYPE_CHECKING:
    from flask import Blueprint

    from .models import FileTranscription

logger = logging.getLogger(__name__)

# Bumped on a change that breaks existing extensions; an additive change keeps it.
API_VERSION = 1
ENTRY_POINT_GROUP = "nexusvox.extensions"
# How long a dictation or selection hook may take before its result is dropped and the text stays as it was.
HOOK_TIMEOUT_S = 3.0

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
    # "upload" for a file, "meeting" for a two-track recording (speakers 1 = Me, 2 = Others). Added without
    # an API_VERSION bump: a hook that ignores it keeps working.
    kind: str = "upload"

    @property
    def best_text(self) -> str:
        """The cleaned text when there is one, else the transcript as transcribed."""
        return self.cleaned_text or self.text


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
    `/api/ext/<id>/`, so its routes are the panel's backend.
    """

    id: str  # lower-case letters, digits, `-` and `_`; unique across all extensions
    title: str
    static_dir: Path
    script: str = "panel.js"
    blueprint: Blueprint | None = None


@dataclass(frozen=True)
class ExportTarget:
    """A way to export a stored file transcription, offered as a download next to it in the Upload tab."""

    id: str  # same rules as `DashboardPanel.id`
    label: str  # shown on the download link
    extension: str  # file name suffix with the dot, e.g. `.docx`
    mimetype: str
    export: Callable[[FileTranscript], bytes | str]
    # Optional, per upload: True/None offers the link; any other falsy answer (False, 0, ...) greys it out;
    # a string greys it out with that text as the tooltip. Called while the Upload list is built, so keep it cheap.
    available: Callable[[int], bool | str | None] | None = None


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

    def on_dictation(self, text: str, app: ActiveApp) -> str | None:
        """Change a dictation just before it is typed into *app*.

        *text* is what would be typed (dictionary, snippets, voice commands, cleanup and translation are
        done). Return the text to type instead, or None to leave it. Blocking is fine (it runs off the UI and
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
    return ""


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
    return ""


@dataclass
class ExtensionRegistry:
    """The loaded extensions and the dispatch of the three hooks."""

    extensions: list[Extension] = field(default_factory=list)
    _panels: dict[str, DashboardPanel] = field(default_factory=dict)
    _targets: dict[str, ExportTarget] = field(default_factory=dict)
    _actions: dict[str, UploadAction] = field(default_factory=dict)
    _stuck: dict[tuple[str, str], threading.Thread] = field(default_factory=dict)

    # ---- Loading -----------------------------------------------------------

    def load(self, entry_point_list: list | None = None) -> None:
        """Load every extension of the entry-point group; skip, and log, any that fails.

        `entry_point_list` replaces the discovery (tests pass fakes).
        """
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

    def dictation(self, text: str, app: ActiveApp) -> str:
        """*text* as every extension's `on_dictation` leaves it, in load order.

        An extension that raises, returns something that is not a string, or does not answer in time is
        skipped: the text stays as the previous step left it. The whole chain shares one `HOOK_TIMEOUT_S`. With
        no extension this returns *text* itself.
        """
        deadline = time.monotonic() + HOOK_TIMEOUT_S
        for extension in self.extensions:
            result = self._text_hook(extension, "on_dictation", deadline, text, app)
            if result is not None:
                text = result
        return text

    def selection_command(self, selection: str, command: str, app: ActiveApp) -> str | None:
        """The replacement for *selection* from the first extension that handles *command*, else None."""
        deadline = time.monotonic() + HOOK_TIMEOUT_S
        for extension in self.extensions:
            result = self._text_hook(extension, "on_selection_command", deadline, selection, command, app)
            if result is not None:
                logger.info("Extension %s handled the selection command %r", extension.name, command)
                return result
        return None

    def _text_hook(self, extension: Extension, hook: str, deadline: float, *args: Any) -> str | None:
        """What one extension's text hook answered, or None: nothing to say, or it failed (logged).

        A hook still running from an earlier timeout is not called again, so a hanging extension costs one
        thread, not one per dictation.
        """
        key = (extension.name, hook)
        stuck = self._stuck.get(key)
        if stuck is not None:
            if stuck.is_alive():
                logger.warning("Extension %s is still busy in %s; skipped", extension.name, hook)
                return None
            self._stuck.pop(key, None)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.error("Extension %s: no time left for %s; skipped", extension.name, hook)
            return None
        worker, outcome = _start_call(getattr(extension, hook), *args)
        worker.join(remaining)
        if not outcome:
            self._stuck[key] = worker
            logger.error("Extension %s did not answer %s in time; skipped", extension.name, hook)
            return None
        ok, value = outcome[0]
        if not ok:
            logger.error("Extension %s failed in %s; skipped", extension.name, hook, exc_info=value)
            return None
        if value is not None and not isinstance(value, str):
            logger.error(
                "Extension %s returned %s from %s, not a string; ignored", extension.name, type(value).__name__, hook
            )
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

    def unavailable_exports(self, transcription_id: int) -> dict[str, str]:
        """The export targets that cannot export this upload, `{target id: reason}`.

        A target without `available` is always offered; one whose check raises is offered too, so the export
        itself reports what is wrong.
        """
        unavailable: dict[str, str] = {}
        for target in self._targets.values():
            if target.available is None:
                continue
            try:
                answer = target.available(transcription_id)
            except Exception:
                logger.exception("Export target %s failed its availability check for %s", target.id, transcription_id)
                continue
            if isinstance(answer, str):
                unavailable[target.id] = answer or "Not available"
            elif answer is not None and not answer:
                unavailable[target.id] = "Not available"
        return unavailable

    def describe(self) -> dict:
        """What the dashboard needs to show the extensions' panels and export links."""
        return {
            "api_version": API_VERSION,
            "extensions": [{"name": e.name, "version": e.version} for e in self.extensions],
            "panels": [
                {"id": p.id, "title": p.title, "script": f"/ext/{p.id}/{p.script}"} for p in self._panels.values()
            ],
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
            _default.load()
            _default_ready = True
    return _default


def loaded_extensions() -> ExtensionRegistry | None:
    """The registry once the entry points are loaded, else None. Never waits: the dictation path uses it so a
    heavy package still loading cannot delay a dictation (which is then typed without extensions)."""
    return _default if _default_ready else None
