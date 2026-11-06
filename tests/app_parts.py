"""Test helper: `NexusVoxApp` is spread over the modules of `nexusvox.app`, and each holds its own imports."""

from __future__ import annotations

import importlib
import pkgutil

import nexusvox.app


def patch_app(monkeypatch, name: str, value: object) -> None:
    """Replace *name* in every part of the app that holds it; a name no part has fails the test.

    Imported lazily, because `nexusvox.app.core` needs the Windows runtime and the tests skip without it.
    """
    parts = [m.name for m in pkgutil.iter_modules(nexusvox.app.__path__)]
    holders = [m for m in (importlib.import_module(f"nexusvox.app.{part}") for part in parts) if hasattr(m, name)]
    assert holders, f"no part of the app has {name}"
    for module in holders:
        monkeypatch.setattr(module, name, value)
