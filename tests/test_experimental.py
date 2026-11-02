"""Features that are not part of v1 (`[experimental]`): hidden and unreachable by default, back with the flag."""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nexusvox.config import EXAMPLE_CONFIG_PATH, Config, ExperimentalConfig, load_config, save_config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI

_STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"

_HIDDEN_ENDPOINTS = [
    ("get", "/api/os-commands"),
    ("post", "/api/os-commands/enabled"),
    ("post", "/api/os-commands/apps"),
    ("get", "/api/flagged"),
    ("post", "/api/flagged/1/correct"),
    ("get", "/api/snippets/github-repos"),
    ("get", "/api/benchmarks"),
    ("get", "/api/benchmarks/compare"),
    ("get", "/api/benchmarks/x.json"),
]


def _client(db, session_factory, **flags):
    app = _create_app(DashboardAPI(session_factory, Config(experimental=ExperimentalConfig(**flags)), db=db))
    app.config["TESTING"] = True
    return app.test_client()


def test_everything_is_off_by_default():
    assert Config().experimental == ExperimentalConfig(False, False, False, False)


def test_config_roundtrip_and_missing_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlanguage = "en"\n', encoding="utf-8")
    assert load_config(path).experimental == ExperimentalConfig()

    save_config(Config(experimental=ExperimentalConfig(os_commands=True, dev_tab=True)), path)
    loaded = load_config(path).experimental
    assert loaded == ExperimentalConfig(os_commands=True, flag_command=False, github_snippets=False, dev_tab=True)


def test_shipped_template_keeps_everything_off():
    text = EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
    section = re.search(r"\[experimental\](.*?)\n\[", text, re.S).group(1)
    assert re.findall(r"^(\w+) = (\w+)$", section, re.M) == [
        ("os_commands", "false"),
        ("flag_command", "false"),
        ("github_snippets", "false"),
        ("dev_tab", "false"),
    ]


@pytest.mark.parametrize(("method", "path"), _HIDDEN_ENDPOINTS)
def test_hidden_endpoints_are_not_registered_by_default(db, session_factory, method, path):
    client = _client(db, session_factory)
    assert getattr(client, method)(path).status_code in (404, 405)  # 405: the static catch-all answers GET only


def test_features_endpoint_reports_the_flags(db, session_factory):
    assert _client(db, session_factory).get("/api/features").get_json() == {
        "os_commands": False,
        "flag_command": False,
        "github_snippets": False,
        "dev_tab": False,
    }
    on = _client(db, session_factory, flag_command=True).get("/api/features").get_json()
    assert on["flag_command"] is True and on["os_commands"] is False


@pytest.mark.parametrize(
    ("flag", "paths"),
    [
        ("os_commands", ["/api/os-commands"]),
        ("flag_command", ["/api/flagged"]),
        ("github_snippets", []),  # shells out to `gh`; only its registration is checked
        ("dev_tab", ["/api/benchmarks", "/api/benchmarks/compare"]),
    ],
)
def test_each_flag_brings_back_only_its_endpoints(db, session_factory, flag, paths):
    client = _client(db, session_factory, **{flag: True})
    for path in paths:
        assert client.get(path).status_code == 200
    registered = {r.rule for r in client.application.url_map.iter_rules()}
    assert ("/api/snippets/github-repos" in registered) == (flag == "github_snippets")
    assert ("/api/os-commands" in registered) == (flag == "os_commands")
    assert ("/api/flagged" in registered) == (flag == "flag_command")
    assert ("/api/benchmarks" in registered) == (flag == "dev_tab")


def test_page_ships_every_experimental_element_hidden():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    for feature in ("os_commands", "flag_command", "github_snippets", "dev_tab"):
        tags = re.findall(rf'<[^>]*data-feature="{feature}"[^>]*>', html)
        assert tags, feature
        assert all(" hidden" in tag for tag in tags), tags
    assert "?dev" not in html
    assert "?dev" not in (_STATIC / "dashboard.js").read_text(encoding="utf-8")


def test_store_audio_hint_does_not_mention_the_edit_tab():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    hint = html[html.index("<h3>Store Audio Recordings</h3>") :][:600]
    assert "Edit tab" not in hint


# ── Voice side (needs the Windows runtime, like test_app_os_control.py) ──────────────────────────────────────────

app_module = pytest.importorskip("nexusvox.app", reason="nexusvox.app needs the Windows runtime", exc_type=ImportError)


def _app(flags: ExperimentalConfig):
    app = app_module.NexusVoxApp.__new__(app_module.NexusVoxApp)
    app._config = Config(experimental=flags)
    app._config.os_commands.enabled = True
    app._config.os_commands.apps = {"chrome": "chrome.exe"}
    app._cue = MagicMock()
    return app


def _run(app, text, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "execute_nexus_command", lambda cmd, cfg: calls.append(cmd.action) or True)
    cmd = app_module.parse_nexus_command(text)
    label = asyncio.run(app._run_command(text, cmd, []))
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
