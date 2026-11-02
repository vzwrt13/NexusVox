"""Where a frozen NexusVox looks for add-on packages (the Pro edition).

A frozen app has no site-packages to `pip install` into. An add-on is unpacked, with its `.dist-info` folder,
into an `extensions` folder next to the exe; that folder goes on `sys.path`, so the add-on imports and its entry
point in the `nexusvox.extensions` group is found like in a normal install.

Security: whatever is in that folder runs inside NexusVox, which holds the microphone, a keyboard hook and the
clipboard. Anyone who can write there can equally replace `NexusVox.exe` itself, so the folder adds no new
boundary as long as the install location is not writable by other users. A per-user install under
`%LOCALAPPDATA%` is writable by the user's own programs, exactly like the exe. There is deliberately no
environment variable or setting that adds another folder.
"""

from __future__ import annotations

import os
import sys


def add_extension_folders(extra: tuple[str, ...] = ()) -> list[str]:
    """Put `<exe folder>/extensions` (and `extra`, used by the self-test) on `sys.path`; return what was added."""
    added = []
    for folder in (os.path.join(os.path.dirname(sys.executable), "extensions"), *extra):
        if os.path.isdir(folder) and folder not in sys.path:
            sys.path.append(folder)
            added.append(folder)
    return added
