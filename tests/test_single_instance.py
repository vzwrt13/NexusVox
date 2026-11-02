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
