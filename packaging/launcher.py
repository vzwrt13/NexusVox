"""Entry point of the frozen (PyInstaller) NexusVox build.

`python -m nexusvox` cannot be frozen directly: `nexusvox/__main__.py` uses relative imports and has no
parent package when PyInstaller runs it as a script. This file imports the package normally instead.

A windowed build (no console) starts with `sys.stdout` and `sys.stderr` set to None. Any `print()` or any
library that writes to them would then raise. Point them at the null device before anything else runs.
"""

from __future__ import annotations

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _add_extension_folders() -> None:
    """Make add-on packages (the Pro edition) importable and visible to `importlib.metadata`.

    A frozen app has no site-packages to `pip install` into, so an add-on is unpacked, with its
    `.dist-info` folder, into `extensions` next to the exe (or the folder named by NEXUSVOX_EXTENSIONS).
    Its entry point in the `nexusvox.extensions` group is then found like in a normal install.
    """
    folders = [os.path.join(os.path.dirname(sys.executable), "extensions")]
    if os.environ.get("NEXUSVOX_EXTENSIONS"):
        folders.append(os.environ["NEXUSVOX_EXTENSIONS"])
    for folder in folders:
        if os.path.isdir(folder) and folder not in sys.path:
            sys.path.append(folder)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import frozen_selftest

        sys.exit(frozen_selftest.main(sys.argv))
    _add_extension_folders()
    from nexusvox.__main__ import main

    main()
