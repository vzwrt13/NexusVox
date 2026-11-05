"""Features that are not part of v1 (`[experimental]`): hidden and unreachable by default, back with the flag."""

import re
from pathlib import Path

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
    ("get", "/api/benchmarks"),
    ("get", "/api/benchmarks/compare"),
    ("get", "/api/benchmarks/x.json"),
]


def _client(db, session_factory, **flags):
    app = _create_app(DashboardAPI(session_factory, Config(experimental=ExperimentalConfig(**flags)), db=db))
    app.config["TESTING"] = True
    return app.test_client()


def test_everything_is_off_by_default():
    assert Config().experimental == ExperimentalConfig(False, False, False)


def test_config_roundtrip_and_missing_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nlanguage = "en"\n', encoding="utf-8")
    assert load_config(path).experimental == ExperimentalConfig()

    save_config(Config(experimental=ExperimentalConfig(os_commands=True, dev_tab=True)), path)
    loaded = load_config(path).experimental
    assert loaded == ExperimentalConfig(os_commands=True, flag_command=False, dev_tab=True)


def test_shipped_template_keeps_everything_off():
    text = EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
    section = re.search(r"\[experimental\](.*?)\n\[", text, re.S).group(1)
    assert re.findall(r"^(\w+) = (\w+)$", section, re.M) == [
        ("os_commands", "false"),
        ("flag_command", "false"),
        ("dev_tab", "false"),
    ]


@pytest.mark.parametrize(("method", "path"), _HIDDEN_ENDPOINTS)
def test_hidden_endpoints_are_not_registered_by_default(db, session_factory, method, path):
    client = _client(db, session_factory)
    # A POST to an unregistered path hits the static catch-all, which answers GET only (405); a GET must be 404.
    assert getattr(client, method)(path).status_code == (405 if method == "post" else 404)


@pytest.mark.parametrize("value", ['"false"', '"true"', "1", '"yes"'])
def test_a_flag_that_is_not_a_real_bool_stays_off(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text("[experimental]\nos_commands = " + value + "\ndev_tab = true\n", encoding="utf-8")
    loaded = load_config(path).experimental
    assert loaded.os_commands is False
    assert loaded.dev_tab is True


def test_features_endpoint_reports_the_flags(db, session_factory):
    assert _client(db, session_factory).get("/api/features").get_json() == {
        "os_commands": False,
        "flag_command": False,
        "dev_tab": False,
    }
    on = _client(db, session_factory, flag_command=True).get("/api/features").get_json()
    assert on["flag_command"] is True and on["os_commands"] is False


@pytest.mark.parametrize(
    ("flag", "paths"),
    [
        ("os_commands", ["/api/os-commands"]),
        ("flag_command", ["/api/flagged"]),
        ("dev_tab", ["/api/benchmarks", "/api/benchmarks/compare"]),
    ],
)
def test_each_flag_brings_back_only_its_endpoints(db, session_factory, flag, paths):
    client = _client(db, session_factory, **{flag: True})
    for path in paths:
        assert client.get(path).status_code == 200
    registered = {r.rule for r in client.application.url_map.iter_rules()}
    assert ("/api/os-commands" in registered) == (flag == "os_commands")
    assert ("/api/flagged" in registered) == (flag == "flag_command")
    assert ("/api/benchmarks" in registered) == (flag == "dev_tab")


def test_page_ships_every_experimental_element_hidden():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    for feature in ("os_commands", "flag_command", "dev_tab"):
        tags = re.findall(rf'<[^>]*data-feature="{feature}"[^>]*>', html)
        assert tags, feature
        assert all(" hidden" in tag for tag in tags), tags
    assert "?dev" not in html
    assert "?dev" not in (_STATIC / "dashboard.js").read_text(encoding="utf-8")


def test_store_audio_hint_does_not_mention_the_edit_tab():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    hint = html[html.index("<h3>Store Audio Recordings</h3>") :][:600]
    assert "Edit tab" not in hint
