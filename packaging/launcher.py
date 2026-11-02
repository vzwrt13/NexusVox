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


if __name__ == "__main__":
    from extension_folders import add_extension_folders

    add_extension_folders()
    if "--selftest" in sys.argv:
        try:
            import frozen_selftest  # only in a build made with `build.py --selftest`
        except ImportError:
            sys.exit(2)
        sys.exit(frozen_selftest.main(sys.argv))
    from nexusvox.__main__ import main

    main()
