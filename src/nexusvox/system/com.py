"""COM setup, shared by everything that talks to WASAPI or UI Automation."""

from __future__ import annotations


def ensure_com() -> None:
    """COM must be initialised on every thread that uses it. Safe to repeat."""
    import comtypes

    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        # RPC_E_CHANGED_MODE: the thread is already in an apartment, which is fine too.
        pass
