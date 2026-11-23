"""Copy diagnostics: the text holds the facts a maintainer needs and never a secret or dictated text."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

import pytest

from nexusvox import __version__
from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.system import diagnostics
from nexusvox.system.logsetup import Text, setup_file_logging

SECRET = "sk-super-secret-123"
SPOKEN = "my private dictated sentence"


@pytest.fixture
def quiet_probes(monkeypatch):
    monkeypatch.setattr(diagnostics, "gpu_name", lambda: "Test GPU")
    monkeypatch.setattr(diagnostics, "cpu_name", lambda: "Test CPU")


@pytest.fixture
def file_log(tmp_path, monkeypatch):
    monkeypatch.setattr("nexusvox.system.logsetup.log_dir", lambda: tmp_path)
    monkeypatch.setattr("nexusvox.system.feedback_send.log_dir", lambda: tmp_path)
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    root.setLevel(logging.INFO)
    setup_file_logging(lambda: True)
    yield
    for handler in root.handlers[:]:
        if handler not in before:
            root.removeHandler(handler)
            if isinstance(handler, TimedRotatingFileHandler):
                handler.close()
    root.setLevel(level)


def _config() -> Config:
    config = Config()
    config.llm.api_key = SECRET
    config.remote_mic.token = SECRET
    config.llm.url = f"https://user:{SECRET}@example.com/v1"
    return config


def test_secrets_never_reach_the_text(quiet_probes):
    text = diagnostics.build_diagnostics(_config())
    assert SECRET not in text
    assert diagnostics.REDACTED in text


def test_an_empty_secret_stays_empty_and_other_values_stay_readable():
    data = diagnostics.redacted_config(Config())
    assert data["llm"]["api_key"] == ""
    assert data["hotkey"]["modifiers"]
    assert "source_path" not in data


def test_dictated_text_in_the_log_is_stripped(quiet_probes, file_log):
    logging.getLogger("nexusvox.test").info("Heard: %s", Text(SPOKEN))
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = diagnostics.build_diagnostics(Config())
    assert SPOKEN not in text
    assert "[dictated text removed]" in text


def test_the_text_names_version_hardware_model_and_packages(quiet_probes):
    text = diagnostics.build_diagnostics(Config(), model_status={"status": "ready"}, device={"requested": "auto"})
    for part in (
        f"App version: {__version__}",
        "Windows:",
        "CPU: Test CPU",
        "GPU: Test GPU",
        "status: ready",
        "flask ",
    ):
        assert part in text


def test_the_log_is_cut_to_200_lines(quiet_probes, file_log):
    for i in range(300):
        logging.getLogger("nexusvox.test").info("line %d", i)
    text = diagnostics.build_diagnostics(Config())
    assert "line 299" in text
    assert "line 50" not in text


def test_the_endpoint_returns_the_text(db, session_factory, quiet_probes):
    api = DashboardAPI(session_factory, Config(), db=db)
    app = _create_app(api)
    with app.test_client() as client:
        resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    assert "NexusVox diagnostics" in resp.get_json()["text"]


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("https://bob:p@ss@example.com/v1", "ss@"),
        ("https://api.example/translate?api_key=XYZ&x=1", "XYZ"),
        ("https://u:sk-abc@host/v1", "sk-abc"),
    ],
)
def test_redact_text_removes_url_credentials_and_secret_query_values(raw, gone):
    out = diagnostics.redact_text(raw)
    assert gone not in out
    assert "example.com" in out or "host" in out or "api.example" in out


def test_a_credentialed_url_in_the_log_is_redacted(quiet_probes, file_log):
    logging.getLogger("nexusvox.test").warning("Warm-up at %s failed", "https://user:sk-abc@host/v1?token=TOK")
    text = diagnostics.build_diagnostics(Config())
    assert "sk-abc" not in text
    assert "TOK" not in text


def test_config_hides_database_paths_and_prints_lists_and_empty_tables_readably(quiet_probes):
    config = Config()
    config.database.path = "D:/Clients/AcmeCorp/nexusvox.db"
    text = diagnostics.build_diagnostics(config)
    assert "AcmeCorp" not in text
    assert "{'" not in text  # no Python repr
