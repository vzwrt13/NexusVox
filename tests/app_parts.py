"""Test helper: `NexusVoxApp` is spread over `app.py` and the `app_*` modules, and each holds its own imports."""

from __future__ import annotations

import importlib
import pkgutil

import nexusvox


def patch_app(monkeypatch, name: str, value: object) -> None:
    """Replace *name* in every part of the app that holds it; a name no part has fails the test.

    Imported lazily, because `nexusvox.app` needs the Windows runtime and the tests skip without it.
    """
    parts = [m.name for m in pkgutil.iter_modules(nexusvox.__path__) if m.name == "app" or m.name.startswith("app_")]
    holders = [m for m in (importlib.import_module(f"nexusvox.{part}") for part in parts) if hasattr(m, name)]
    assert holders, f"no part of the app has {name}"
    for module in holders:
        monkeypatch.setattr(module, name, value)
