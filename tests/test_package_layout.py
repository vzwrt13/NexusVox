"""What the package layout promises: stable logger names, one-way dependencies, empty package inits, Pro's paths."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import nexusvox

PACKAGE = Path(nexusvox.__file__).parent
SUBPACKAGES = ("app", "audio", "dictation", "meetings", "storage", "system", "transcription")
# The lowest layers: everything may use them, so they use none of the packages built on top of them.
LOW_LAYERS = ("storage", "system")
HIGHER_LAYERS = {"app", "audio", "dashboard", "dictation", "meetings", "transcribers", "transcription"}

# Log lines print these. A module keeps its name when it moves, so an entry here only changes on purpose.
FIXED_LOGGERS = {
    "__main__.py": "nexusvox",
    "app/common.py": "nexusvox.app",
    "audio/capture.py": "nexusvox.audio",
    "audio/mic_guard.py": "nexusvox.mic_guard",
    "audio/remote_mic.py": "nexusvox.remote_mic",
    "dashboard/api_base.py": "nexusvox.dashboard.api",
    "dashboard/routes_extensions.py": "nexusvox.dashboard",
    "dictation/assistant.py": "nexusvox.assistant",
    "dictation/cleanup.py": "nexusvox.cleanup",
    "dictation/edit_watch.py": "nexusvox.edit_watch",
    "dictation/hotkey.py": "nexusvox.hotkey",
    "dictation/injector.py": "nexusvox.injector",
    "dictation/os_commands.py": "nexusvox.os_commands",
    "dictation/overlay.py": "nexusvox.overlay",
    "dictation/pipeline.py": "nexusvox.pipeline",
    "dictation/polish.py": "nexusvox.polish",
    "dictation/translator.py": "nexusvox.translator",
    "meetings/live.py": "nexusvox.meeting_live",
    "meetings/recorder.py": "nexusvox.meeting_recorder",
    "meetings/session.py": "nexusvox.meeting_session",
    "meetings/transcribe.py": "nexusvox.meeting_transcribe",
    "storage/db.py": "nexusvox.db",
    "system/docker_ctl.py": "nexusvox.docker_ctl",
    "system/keyboard.py": "nexusvox.injector",
    "system/single_instance.py": "nexusvox.single_instance",
    "system/uia.py": "nexusvox.uia",
    "system/window_manager.py": "nexusvox.window_manager",
    "transcription/file_transcribe.py": "nexusvox.file_transcribe",
    "transcription/transcriber.py": "nexusvox.transcriber",
}


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, path.relative_to(PACKAGE).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _runtime_imports(node, folder: list[str]):
    """The targets `node` imports at run time, lazy ones included; `if TYPE_CHECKING:` blocks do not run."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If) and "TYPE_CHECKING" in ast.dump(child.test):
            continue
        if isinstance(child, ast.ImportFrom) and child.level:
            base = folder[: len(folder) - (child.level - 1)]
            if child.module:
                yield [*base, *child.module.split(".")]
            else:
                yield from ([*base, alias.name] for alias in child.names)
        yield from _runtime_imports(child, folder)


def test_logger_names_stay_what_log_lines_print():
    found = {}
    for _path, name, tree in _modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value.startswith("nexusvox")
            ):
                found[name] = node.args[0].value
    assert found == FIXED_LOGGERS


@pytest.mark.parametrize("package", LOW_LAYERS)
def test_the_low_layers_import_nothing_built_on_them(package):
    wrong = []
    for path, name, tree in _modules():
        if name.startswith(f"{package}/"):
            folder = [package, *path.relative_to(PACKAGE / package).parts[:-1]]
            wrong += [f"{name} -> {target}" for target in _runtime_imports(tree, folder) if target[0] in HIGHER_LAYERS]
    assert not wrong


@pytest.mark.parametrize("package", SUBPACKAGES)
def test_a_package_init_is_a_docstring_only(package):
    """nexusvox-pro installs the core without its audio and GPU dependencies: a package must not load its modules."""
    body = ast.parse((PACKAGE / package / "__init__.py").read_text(encoding="utf-8")).body
    assert len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)


@pytest.mark.parametrize("module", ["config", "extensions", "llm", "snippets", "file_cleanup"])
def test_the_paths_nexusvox_pro_imports_stay_real_modules(module):
    assert (PACKAGE / f"{module}.py").is_file()
