"""Import the scripts in packaging/ for the tests that cover them (not a test module itself)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def load_packaging_script(name: str) -> ModuleType:
    """Import `packaging/<name>.py` by path. The folder is not a package, and putting the repository root on sys.path
    would make `import packaging` ambiguous with the PyPA library the scripts use. The folder is on sys.path while
    the script loads, because the scripts import their siblings by name, as they do when run as scripts."""
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(PACKAGING))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(PACKAGING))
    return module
