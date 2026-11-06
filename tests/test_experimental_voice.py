"""The voice side of `[experimental]`: OS commands and "nexus flag" do nothing unless their flag is on."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from app_parts import patch_app

from nexusvox.config import Config, ExperimentalConfig
from nexusvox.extensions import HOOK_TIMEOUT_S, ActiveApp

if sys.platform != "win32":  # pynput hook, tray, SendInput and PortAudio imports; on Windows a failure must show
    pytest.skip("nexusvox.app needs the Windows runtime", allow_module_level=True)

import nexusvox.app as app_module  # noqa: E402
from nexusvox.os_commands import parse_nexus_command  # noqa: E402
from nexusvox.overlay import ERROR  # noqa: E402


def _app(flags: ExperimentalConfig):
    app = app_module.NexusVoxApp.__new__(app_module.NexusVoxApp)
    app._config = Config(experimental=flags)
    app._config.os_commands.enabled = True
    app._config.os_commands.apps = {"chrome": "chrome.exe"}
    app._cue = MagicMock()
    return app


def _run(app, text, monkeypatch):
    calls = []
    patch_app(monkeypatch, "execute_nexus_command", lambda cmd, cfg: calls.append(cmd.action) or True)
    cmd = parse_nexus_command(text)
    label = asyncio.run(app._run_command(text, cmd))
    return label, calls


def test_os_commands_do_not_run_by_default_even_when_enabled_in_the_old_section(monkeypatch):
    label, calls = _run(_app(ExperimentalConfig()), "nexus minimize chrome", monkeypatch)
    assert label is None and calls == []


def test_os_commands_run_with_the_flag(monkeypatch):
    label, calls = _run(_app(ExperimentalConfig(os_commands=True)), "nexus minimize chrome", monkeypatch)
    assert label == "[nexus] minimize chrome" and calls == ["minimize"]


def test_the_flag_command_is_never_an_os_command(monkeypatch):
    label, calls = _run(_app(ExperimentalConfig(os_commands=True)), "nexus flag", monkeypatch)
    assert label is None and calls == []


def _flag_cycle(monkeypatch, flags):
    from test_app_os_control import _dictation_app, _run_cycle  # noqa: PLC0415

    app, _ = _dictation_app(monkeypatch, lambda *a, **k: None)
    app._config = Config(experimental=flags)
    app._config.injection_delay_ms = 0
    app._transcriber.finish = AsyncMock(return_value=MagicMock(text="nexus flag", confidence=None))
    app._run_command = AsyncMock(return_value=None)
    app._flag_last_transcription = MagicMock()
    _run_cycle(app)
    return app


def test_nexus_flag_is_typed_like_any_text_by_default(monkeypatch):
    app = _flag_cycle(monkeypatch, ExperimentalConfig())
    app._flag_last_transcription.assert_not_called()
    app._run_command.assert_awaited_once()


def test_nexus_flag_flags_with_the_flag(monkeypatch):
    app = _flag_cycle(monkeypatch, ExperimentalConfig(flag_command=True))
    app._flag_last_transcription.assert_called_once()
    app._run_command.assert_not_awaited()


# -- on_command: an extension consumes a dictation before the core's own commands ----------------------


def _registry_with(answer):
    from nexusvox.extensions import API_VERSION, Extension, ExtensionRegistry

    class Opens(Extension):
        name = "opens"
        api_version = API_VERSION

        def __init__(self):
            self.seen = []

        def on_command(self, text, app):
            self.seen.append((text, app))
            return answer(text)

    registry = ExtensionRegistry()
    extension = Opens()
    assert registry.add(extension)
    return registry, extension


def _command_app(registry, monkeypatch, flags=None):
    app = _app(flags or ExperimentalConfig())
    patch_app(monkeypatch, "loaded_extensions", lambda: registry)
    monkeypatch.setattr(app_module.NexusVoxApp, "_active_app", classmethod(lambda cls: ActiveApp("Code.exe", "title")))
    return app


def test_an_extension_command_is_saved_under_its_label_and_not_typed(monkeypatch):
    registry, extension = _registry_with(lambda text: ("[nexus] open https://a", True))
    app = _command_app(registry, monkeypatch)
    label = asyncio.run(app._run_command("nexus open my site", None))
    assert label == "[nexus] open https://a"
    assert extension.seen == [("nexus open my site", ActiveApp("Code.exe", "title"))]
    app._cue.assert_not_called()


def test_an_extension_command_that_did_not_open_plays_the_error_cue(monkeypatch):
    registry, _ = _registry_with(lambda text: ("[nexus] open nowhere", False))
    app = _command_app(registry, monkeypatch)
    assert asyncio.run(app._run_command("nexus öffne nowhere", None)) == "[nexus] open nowhere"
    app._cue.assert_called_once()
    assert app._cue.call_args.args[1] == ERROR


def test_an_extension_runs_before_the_core_commands(monkeypatch):
    registry, _ = _registry_with(lambda text: ("[ext] minimize", True))
    app = _command_app(registry, monkeypatch, ExperimentalConfig(os_commands=True))
    calls = []
    patch_app(monkeypatch, "execute_nexus_command", lambda cmd, cfg: calls.append(cmd.action) or True)
    cmd = parse_nexus_command("nexus minimize chrome")
    assert asyncio.run(app._run_command("nexus minimize chrome", cmd)) == "[ext] minimize"
    assert calls == []


def test_a_command_no_extension_takes_falls_through_to_the_core(monkeypatch):
    registry, extension = _registry_with(lambda text: None)
    app = _command_app(registry, monkeypatch, ExperimentalConfig(os_commands=True))
    calls = []
    patch_app(monkeypatch, "execute_nexus_command", lambda cmd, cfg: calls.append(cmd.action) or True)
    cmd = parse_nexus_command("nexus minimize chrome")
    assert asyncio.run(app._run_command("nexus minimize chrome", cmd)) == "[nexus] minimize chrome"
    assert calls == ["minimize"] and len(extension.seen) == 1


def test_nexus_open_is_typed_when_no_extension_takes_it(monkeypatch):
    registry, _ = _registry_with(lambda text: None)
    app = _command_app(registry, monkeypatch)
    assert asyncio.run(app._run_command("nexus open my website", None)) is None
    patch_app(monkeypatch, "loaded_extensions", lambda: None)  # extensions still loading
    assert asyncio.run(app._run_command("nexus öffne meine Webseite", None)) is None
    app._cue.assert_not_called()


def test_a_registry_that_blows_up_never_loses_the_dictation(monkeypatch):
    registry, _ = _registry_with(lambda text: ("[x] y", True))

    def boom(*_a, **_k):
        raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(registry, "command", boom)
    app = _command_app(registry, monkeypatch)
    assert asyncio.run(app._run_command("nexus open my site", None)) is None


def test_no_command_hook_means_no_window_lookup_and_the_budget_stays_whole(monkeypatch):
    from nexusvox.extensions import API_VERSION, Extension, ExtensionRegistry

    class Plain(Extension):
        name = "plain"
        api_version = API_VERSION

    registry = ExtensionRegistry()
    assert registry.add(Plain())
    app = _command_app(registry, monkeypatch)
    looked = []
    monkeypatch.setattr(app_module.NexusVoxApp, "_active_app", classmethod(lambda cls: looked.append(1) or ActiveApp()))
    assert asyncio.run(app._run_command("hello there", None)) is None
    assert looked == [] and app._hook_budget_s == HOOK_TIMEOUT_S


def test_on_command_time_is_taken_from_the_on_dictation_budget(monkeypatch):
    import time

    def slow(text):
        time.sleep(0.2)
        return None

    registry, _ = _registry_with(slow)
    app = _command_app(registry, monkeypatch)
    app._hook_budget_s = HOOK_TIMEOUT_S
    asyncio.run(app._run_command("hello there", None))
    assert app._hook_budget_s < HOOK_TIMEOUT_S - 0.15


# -- the protect hook on the app side ------------------------------------------------------------------------


def _protect_registry():
    from nexusvox.extensions import API_VERSION, Extension, ExtensionRegistry

    class Shield(Extension):
        name = "shield"
        api_version = API_VERSION

        def __init__(self):
            self.apps = []

        def protect(self, text, app):
            self.apps.append(app)
            return text.replace("my address", "⟦1⟧"), lambda out: out.replace("⟦1⟧", "Hauptstrasse 1")

    registry = ExtensionRegistry()
    extension = Shield()
    assert registry.add(extension)
    return registry, extension


def test_protect_gets_the_active_app_for_a_pc_dictation_and_an_empty_one_for_the_phone(monkeypatch):
    registry, extension = _protect_registry()
    app = _command_app(registry, monkeypatch)
    protected, restore = app._protect_dictation("send my address")
    assert protected == "send ⟦1⟧" and restore("send ⟦1⟧") == "send Hauptstrasse 1"
    protected, restore = app._protect_dictation("send my address", remote=True)
    assert restore("send ⟦1⟧") == "send Hauptstrasse 1"
    assert extension.apps == [ActiveApp("Code.exe", "title"), ActiveApp()]


def test_without_a_protect_hook_there_is_no_window_lookup_and_the_budget_stays_whole(monkeypatch):
    registry, _ = _registry_with(lambda text: None)  # only on_command
    app = _command_app(registry, monkeypatch)
    looked = []
    monkeypatch.setattr(app_module.NexusVoxApp, "_active_app", classmethod(lambda cls: looked.append(1) or ActiveApp()))
    assert app._protect_dictation("hello there") is None
    assert app._protect_dictation("hello there", remote=True) is None
    assert looked == [] and app._hook_budget_s == HOOK_TIMEOUT_S


def test_no_registry_yet_means_no_protection(monkeypatch):
    app = _app(ExperimentalConfig())
    patch_app(monkeypatch, "loaded_extensions", lambda: None)
    assert app._protect_dictation("send my address") is None


def test_protect_and_restore_time_come_out_of_the_shared_budget_but_not_the_pipeline_in_between(monkeypatch):
    import time

    from nexusvox.extensions import API_VERSION, Extension, ExtensionRegistry

    class Pause(Extension):
        name = "pause"
        api_version = API_VERSION

        def protect(self, text, app):
            time.sleep(0.2)
            return text, self.restore

        def restore(self, text):
            time.sleep(0.2)
            return text

    registry = ExtensionRegistry()
    assert registry.add(Pause())
    app = _command_app(registry, monkeypatch)
    app._hook_budget_s = HOOK_TIMEOUT_S
    _, restore = app._protect_dictation("hello there")
    after_protect = app._hook_budget_s
    assert after_protect < HOOK_TIMEOUT_S - 0.15
    time.sleep(0.3)  # cleanup and translation: not charged
    assert app._hook_budget_s == after_protect
    assert restore("hello there") == "hello there"
    assert app._hook_budget_s < after_protect - 0.15
