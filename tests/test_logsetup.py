"""File logging: daily rotation, cleanup of old files, shortened dictation text and the dashboard controls."""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler

import pytest

from nexusvox import logsetup
from nexusvox.config import Config, load_config, save_config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.logsetup import Text, setup_file_logging, shorten


@pytest.fixture
def file_log(tmp_path):
    """Installs the file handler on the root logger for one test; yields (log file, full-text switch)."""
    full = {"on": False}
    root = logging.getLogger()
    before = list(root.handlers)
    previous = root.level
    root.setLevel(logging.INFO)
    path = setup_file_logging(lambda: full["on"], directory=tmp_path / "logs")
    try:
        yield path, full
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(previous)


def _read(path):
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_log_dir_is_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert logsetup.log_dir() == tmp_path / "NexusVox" / "logs"


def test_records_reach_a_daily_rotating_file(file_log):
    path, _ = file_log
    logging.getLogger("nexusvox.test").info("hello file")
    assert path.name == "nexusvox.log" and "hello file" in _read(path)
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler))
    assert handler.when == "MIDNIGHT"


def test_rotation_moves_the_day_to_a_dated_file(file_log):
    path, _ = file_log
    # Straight into the handler, so the logger levels other tests left behind cannot matter.
    handler = next(h for h in logging.getLogger().handlers if getattr(h, "baseFilename", None) == str(path))

    def write(message):
        handler.handle(logging.makeLogRecord({"msg": message, "levelno": logging.INFO, "levelname": "INFO"}))

    write("day one")
    assert "day one" in path.read_text(encoding="utf-8")
    handler.doRollover()
    write("day two")
    rotated = [p for p in path.parent.iterdir() if p.name.startswith("nexusvox.log.")]
    assert len(rotated) == 1, sorted(p.name for p in path.parent.iterdir())
    assert "day one" in rotated[0].read_text(encoding="utf-8")
    assert "day two" in path.read_text(encoding="utf-8") and "day one" not in path.read_text(encoding="utf-8")


def test_files_older_than_seven_days_are_deleted_at_start(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    old, fresh, other = folder / "nexusvox.log.2026-01-01", folder / "nexusvox.log.2026-10-27", folder / "notes.txt"
    for p in (old, fresh, other):
        p.write_text("x")
    long_ago = time.time() - 8 * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    assert logsetup.delete_old_logs(folder) == 1
    assert not old.exists() and fresh.exists() and other.exists()  # only NexusVox's own log files go


def test_setup_removes_old_files_and_survives_an_unwritable_folder(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    stale = folder / "nexusvox.log.2025-01-01"
    stale.write_text("x")
    os.utime(stale, (1, 1))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert setup_file_logging(lambda: False, directory=folder) is not None
        assert not stale.exists()
        blocker = tmp_path / "file"
        blocker.write_text("x")
        assert setup_file_logging(lambda: False, directory=blocker / "sub") is None
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def test_shorten_keeps_the_start_and_the_length():
    assert shorten("a" * 25) == "'" + "a" * 20 + "...' (25 chars)"
    assert shorten("hi there") == "'hi there' (8 chars)"


def test_file_gets_shortened_text_for_s_and_r_and_the_console_stays_full(file_log, caplog):
    path, _ = file_log
    spoken = "my secret sentence that goes on and on"
    with caplog.at_level(logging.INFO):
        logging.getLogger("nexusvox.test").info("done: %s / %r", Text(spoken), Text(spoken))
    assert spoken in caplog.text  # other handlers (the console) see the original arguments
    content = _read(path)
    assert "my secret sentence t" in content and "secret sentence that goes" not in content
    assert f"({len(spoken)} chars)" in content


def test_full_text_setting_writes_the_whole_text(file_log):
    path, full = file_log
    full["on"] = True
    logging.getLogger("nexusvox.test").info("done: %s", Text("the whole dictated sentence"))
    assert "the whole dictated sentence" in _read(path)


def test_the_setting_defaults_off_and_round_trips(tmp_path):
    assert Config().log_full_text is False
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    assert cfg.log_full_text is False
    cfg.log_full_text = True
    save_config(cfg, path)
    assert load_config(path).log_full_text is True


def test_dashboard_toggles_the_setting_and_opens_the_folder(db, session_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    opened = []
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)
    monkeypatch.setattr("nexusvox.dashboard.api.os.startfile", opened.append, raising=False)
    config = Config()
    app = _create_app(DashboardAPI(session_factory, config, db=db))
    with app.test_client() as client:
        settings = client.get("/api/settings").get_json()
        assert settings["log_full_text"] is False and settings["log_dir"] == str(tmp_path / "NexusVox" / "logs")
        assert client.post("/api/settings/log-full-text", json={"enabled": True}).get_json()["log_full_text"] is True
        assert config.log_full_text is True
        assert client.post("/api/log-folder/open").get_json()["ok"] is True
    assert [str(p) for p in opened] == [str(tmp_path / "NexusVox" / "logs")]


def test_a_failed_rollover_is_skipped_instead_of_retried_on_every_record(file_log, monkeypatch):
    path, _ = file_log
    handler = next(h for h in logging.getLogger().handlers if getattr(h, "baseFilename", None) == str(path))
    handler.rolloverAt = 0  # a day has passed

    def refused(self):
        raise PermissionError("another process holds the file")

    monkeypatch.setattr(TimedRotatingFileHandler, "doRollover", refused)
    handler.handle(logging.makeLogRecord({"msg": "after midnight", "levelno": logging.INFO, "levelname": "INFO"}))
    handler.handle(logging.makeLogRecord({"msg": "still logging", "levelno": logging.INFO, "levelname": "INFO"}))
    assert handler.rolloverAt > time.time()
    assert "after midnight" in path.read_text(encoding="utf-8") and "still logging" in path.read_text(encoding="utf-8")


def test_rollover_deletes_files_past_the_retention(file_log):
    path, _ = file_log
    handler = next(h for h in logging.getLogger().handlers if getattr(h, "baseFilename", None) == str(path))
    old = path.parent / "nexusvox.log.2020-01-01"
    old.write_text("x")
    os.utime(old, (1, 1))
    handler.handle(logging.makeLogRecord({"msg": "today", "levelno": logging.INFO, "levelname": "INFO"}))
    handler.doRollover()
    assert not old.exists()


def test_the_dashboard_refuses_a_non_boolean_switch(db, session_factory, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)
    config = Config()
    with _create_app(DashboardAPI(session_factory, config, db=db)).test_client() as client:
        for bad in ("false", 0, None):
            assert client.post("/api/settings/log-full-text", json={"enabled": bad}).status_code == 400
        assert client.post("/api/settings/log-full-text", json={}).status_code == 400
    assert config.log_full_text is False


def test_open_log_folder_says_so_where_startfile_does_not_exist(db, session_factory, monkeypatch):
    monkeypatch.delattr("nexusvox.dashboard.api.os.startfile", raising=False)
    with _create_app(DashboardAPI(session_factory, Config(), db=db)).test_client() as client:
        resp = client.post("/api/log-folder/open")
    assert resp.status_code == 500 and "only supported on Windows" in resp.get_json()["error"]


# Names that carry what the user said or what a model made of it. A logger call passing one of them must wrap it
# in Text, or the full dictation lands in the file log.
_SPOKEN_NAMES = {
    "text", "raw", "corrected", "typed", "_last_typed", "delta", "target", "command", "cleaned", "translated",
    "reply", "assistant_text", "spoken", "instruction",
}  # fmt: skip


def _unwrapped_spoken_log_arguments():
    import ast
    from pathlib import Path

    found = []
    for source in sorted(Path(logsetup.__file__).parent.rglob("*.py")):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            ):
                continue
            for arg in node.args[1:]:
                name = arg.id if isinstance(arg, ast.Name) else arg.attr if isinstance(arg, ast.Attribute) else None
                if name in _SPOKEN_NAMES:
                    found.append(f"{source.name}:{node.lineno} passes {name} without Text()")
    return found


def test_no_log_call_passes_spoken_text_without_the_marker():
    assert _unwrapped_spoken_log_arguments() == []
