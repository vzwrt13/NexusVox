"""Optional extensions, discovered through Python entry points.

An installed package registers an `Extension` under the `nexusvox.extensions` entry-point group:

    [project.entry-points."nexusvox.extensions"]
    my_extension = "my_package:MyExtension"

The entry point may name an `Extension` subclass, an instance, or a zero-argument function returning one.
NexusVox loads every extension once at start. An extension that cannot be imported, targets another
`API_VERSION`, is built wrongly or raises in a hook is logged and skipped; it never stops the app.
With no extension installed nothing changes. The hooks are documented in docs/EXTENSIONS.md.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .transcriber import Word

if TYPE_CHECKING:
    from flask import Blueprint

    from .models import FileTranscription

logger = logging.getLogger(__name__)

# Bumped on a change that breaks existing extensions; an additive change keeps it.
API_VERSION = 1
ENTRY_POINT_GROUP = "nexusvox.extensions"

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
    )


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

    def dashboard_panels(self) -> list[DashboardPanel]:
        """Extra dashboard tabs."""
        return []

    def export_targets(self) -> list[ExportTarget]:
        """Extra ways to export a stored file transcription."""
        return []


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


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
    return ""


@dataclass
class ExtensionRegistry:
    """The loaded extensions and the dispatch of the three hooks."""

    extensions: list[Extension] = field(default_factory=list)
    _panels: dict[str, DashboardPanel] = field(default_factory=dict)
    _targets: dict[str, ExportTarget] = field(default_factory=dict)

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
        except Exception:
            logger.exception("Extension %r failed while registering and is skipped", source or loaded)
            return False
        self._panels.update({p.id: p for p in panels})
        self._targets.update({t.id: t for t in targets})
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

    # ---- Hooks: dashboard panels and export targets --------------------------

    def panels(self) -> list[DashboardPanel]:
        return list(self._panels.values())

    def panel(self, panel_id: str) -> DashboardPanel | None:
        return self._panels.get(panel_id)

    def export_targets(self) -> list[ExportTarget]:
        return list(self._targets.values())

    def export_target(self, target_id: str) -> ExportTarget | None:
        return self._targets.get(target_id)

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
        }


_default = ExtensionRegistry()
_default_loaded = False
_default_lock = threading.RLock()


def load_extensions() -> ExtensionRegistry:
    """The process-wide registry, filled from the installed entry points on the first call."""
    global _default_loaded
    with _default_lock:  # reentrant: an extension calling this while it loads gets the registry as filled so far
        if not _default_loaded:
            _default_loaded = True
            _default.load()
    return _default
