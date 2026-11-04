"""A second start finds the running instance, rings it and does not become a second instance."""

import sys
import threading
import uuid

import pytest

from nexusvox import single_instance
from nexusvox.single_instance import SingleInstance

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named kernel objects are Windows-only")


@pytest.fixture(autouse=True)
def private_names(monkeypatch):
    """Own object names per test, so a NexusVox that really runs on this machine does not interfere."""
    tag = uuid.uuid4().hex
    monkeypatch.setattr(single_instance, "MUTEX_NAME", f"Local\\NexusVox-test-{tag}")
    monkeypatch.setattr(single_instance, "EVENT_NAME", f"Local\\NexusVox-test-ring-{tag}")


def test_the_first_start_wins_and_a_second_does_not():
    first, second = SingleInstance(), SingleInstance()
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()


def test_a_second_start_rings_the_running_instance():
    first, second = SingleInstance(), SingleInstance()
    assert first.acquire()
    rang = threading.Event()
    first.watch(rang.set)

    assert second.acquire() is False
    assert second.ring_running_instance() is True
    assert rang.wait(5)
    first.release()


def test_ringing_with_nobody_running_reports_failure():
    assert SingleInstance().ring_running_instance() is False


def test_main_exits_on_a_second_start_before_building_the_app(monkeypatch):
    import importlib
    import types

    built = []
    messages = []
    monkeypatch.setitem(
        sys.modules, "nexusvox.app", types.SimpleNamespace(NexusVoxApp=lambda config: built.append(config))
    )
    monkeypatch.delitem(sys.modules, "nexusvox.__main__", raising=False)
    entry = importlib.import_module("nexusvox.__main__")
    rung = []
    fake = types.SimpleNamespace(acquire=lambda: False, ring_running_instance=lambda: rung.append(1) or True)
    monkeypatch.setattr(entry, "SingleInstance", lambda: fake)
    monkeypatch.setattr(entry, "show_message", messages.append)
    monkeypatch.setattr(entry, "ensure_dirs", lambda: built.append("dirs"))
    try:
        with pytest.raises(SystemExit) as exit_:
            entry.main()
    finally:
        sys.modules.pop("nexusvox.__main__", None)
    assert exit_.value.code == 0
    assert rung == [1] and built == [] and messages == []


def test_release_hands_the_mutex_back_for_a_restart():
    first, second = SingleInstance(), SingleInstance()
    assert first.acquire()
    first.watch(lambda: None)
    first.release()

    assert second.acquire() is True
    second.release()


def test_access_denied_on_the_mutex_counts_as_already_running(monkeypatch):
    class Denied:
        def CreateEventW(self, *args):  # noqa: N802
            return None

        def CreateMutexW(self, *args):  # noqa: N802
            return None

        CloseHandle = staticmethod(lambda handle: True)  # noqa: N815
        LocalFree = staticmethod(lambda handle: None)  # noqa: N815

    monkeypatch.setattr(single_instance, "_kernel32", lambda: Denied())
    monkeypatch.setattr(single_instance.ctypes, "get_last_error", lambda: 5)
    assert SingleInstance().acquire() is False


def test_the_mutex_can_be_opened_to_wait_on_it():
    """The installer's AppMutex check opens the mutex with SYNCHRONIZE only; that must work for any logged-on user."""
    import ctypes

    synchronize = 0x00100000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.c_wchar_p]
    k32.OpenMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]

    first = SingleInstance()
    assert first.acquire()
    handle = k32.OpenMutexW(synchronize, False, single_instance.MUTEX_NAME)
    assert handle
    k32.CloseHandle(handle)
    first.release()
