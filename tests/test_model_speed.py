"""Which language model and device produced a dictation's timings, and the "Speed by model" table."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from nexusvox.config import CleanupConfig, CustomServerConfig, DatabaseConfig, InferenceConfig, LlmConfig
from nexusvox.dashboard.analytics import get_model_speed
from nexusvox.dictation.cleanup import cleanup
from nexusvox.storage.db import Database
from nexusvox.storage.models import COMMAND_APP, Transcription
from nexusvox.transcription.transcriber import create_transcriber


def _row(model="gemma", level="light", device="gpu", status="applied", ms=200, **extra):
    row = {
        "text": "a",
        "language": "en",
        "duration_ms": 1000,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
        "llm_model": model,
        "cleanup_level": level,
        "transcribe_device": device,
        "cleanup_status": status,
        "cleanup_ms": ms,
    }
    row.update(extra)
    return row


def _seed(session_factory, rows):
    with session_factory() as session:
        for r in rows:
            session.add(Transcription(**r))
        session.commit()


# ---- Storing -----------------------------------------------------------------------------------


def test_the_new_fields_are_stored(db):
    record = db.save_transcription(
        "hi",
        "en",
        500,
        cleanup_status="applied",
        cleanup_ms=300,
        llm_model="gemma4:12b",
        llm_url="http://x:11434",
        cleanup_level="medium",
        transcribe_device="cpu",
    )
    stored = db.get_recent(1)[0]
    assert record.id == stored.id
    assert (stored.llm_model, stored.llm_url, stored.cleanup_level, stored.transcribe_device) == (
        "gemma4:12b",
        "http://x:11434",
        "medium",
        "cpu",
    )


def test_a_row_without_them_has_null_columns(db):
    db.save_transcription("hi", "en", 500)
    stored = db.get_recent(1)[0]
    assert (stored.llm_model, stored.llm_url, stored.cleanup_level, stored.transcribe_device) == (None,) * 4


def test_an_old_database_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE transcriptions (id INTEGER PRIMARY KEY, text TEXT NOT NULL,"
            " language VARCHAR(5) NOT NULL, duration_ms INTEGER NOT NULL, created_at DATETIME, confidence FLOAT,"
            " cleanup_status VARCHAR(10), cleanup_ms INTEGER)"
        )
        conn.execute(
            "INSERT INTO transcriptions (text, language, duration_ms, cleanup_status, cleanup_ms)"
            " VALUES ('old row', 'en', 1, 'applied', 250)"
        )

    migrated = Database(DatabaseConfig(path=str(path)))

    old = migrated.get_recent(1)[0]
    assert (old.text, old.cleanup_ms) == ("old row", 250)
    assert (old.llm_model, old.llm_url, old.cleanup_level, old.transcribe_device) == (None,) * 4
    migrated.save_transcription("new", "en", 1, llm_model="m", transcribe_device="gpu")
    assert migrated.get_recent(1)[0].llm_model == "m"


# ---- What the cleanup and the transcribers hand over ----------------------------------------------------


def test_cleanup_says_which_model_it_asked(monkeypatch):
    from nexusvox.dictation import cleanup as module

    monkeypatch.setattr(module, "complete", lambda *_: "Ich komme heute später.")
    llm = LlmConfig(url="http://llm:1234", model="qwen")
    result = cleanup("Ähm ich komme äh heute später", CleanupConfig(level="medium"), llm)
    assert (result.llm_model, result.llm_url, result.level) == ("qwen", "http://llm:1234", "medium")


def test_cleanup_that_never_asked_names_no_model():
    result = cleanup("Ähm ich komme äh heute später", CleanupConfig(), LlmConfig())
    assert (result.llm_model, result.llm_url, result.level) == (None, None, None)
    assert result.status is None


def test_the_transcription_device_follows_the_backend():
    assert create_transcriber(InferenceConfig(model="parakeet-tdt-0.6b"), device="cuda").device == "gpu"
    assert create_transcriber(InferenceConfig(model="parakeet-tdt-0.6b"), device="cpu").device == "cpu"
    assert create_transcriber(InferenceConfig(model="whisper-large-v3-turbo"), device="cpu").device == "cpu"
    far = InferenceConfig(model="parakeet-tdt-0.6b", server_url="http://gpubox:8002/v1/audio/transcriptions")
    assert create_transcriber(far, device="cuda").device == "remote"
    custom = InferenceConfig(model="custom-server", custom=CustomServerConfig(url="http://localhost:9/v1"))
    assert create_transcriber(custom, device="cuda").device == "remote"  # a server the app does not run


def test_the_pc_knows_its_own_host_name():
    import socket

    from nexusvox.transcription.transcriber import _this_pc_names

    assert socket.gethostname().lower() in _this_pc_names()


def test_every_way_of_naming_this_pc_is_local(monkeypatch):
    from nexusvox.transcription import transcriber as module

    monkeypatch.setattr(
        module,
        "_this_pc_names",
        lambda: frozenset({"localhost", "host.docker.internal", "desktop-abc", "192.168.1.20"}),
    )
    for host in ("localhost", "127.0.1.1", "[::1]", "host.docker.internal", "DESKTOP-ABC", "192.168.1.20"):
        assert module._is_local_url(f"http://{host}:8002/v1/audio/transcriptions"), host
    for host in ("gpubox", "192.168.1.99", "example.com"):
        assert not module._is_local_url(f"http://{host}:8002/v1/audio/transcriptions"), host


# ---- The table ---------------------------------------------------------------------------------


def test_empty_database_has_no_rows(session_factory):
    assert get_model_speed(session_factory) == []


def test_groups_by_model_level_and_device(session_factory):
    _seed(
        session_factory,
        [
            _row(ms=100),
            _row(ms=300),
            _row(ms=900),
            _row(status="timeout", ms=None),
            _row(level="medium", ms=500),
            _row(device="cpu", ms=2000),
            _row(model="qwen", ms=50),
        ],
    )

    rows = get_model_speed(session_factory)

    assert rows[0] == {
        "model": "gemma",
        "server": None,
        "level": "light",
        "device": "gpu",
        "count": 4,
        "median_ms": 300,
        "timeout_share": 0.25,
    }
    assert [(r["model"], r["level"], r["device"], r["count"]) for r in rows[1:]] == [
        ("gemma", "light", "cpu", 1),
        ("gemma", "medium", "gpu", 1),
        ("qwen", "light", "gpu", 1),
    ]


def test_rows_from_before_the_model_was_stored_are_left_out(session_factory):
    _seed(session_factory, [_row(model=None, level=None, device=None), _row()])

    assert [r["count"] for r in get_model_speed(session_factory)] == [1]


def test_polish_attempts_are_their_own_level_and_extensions_are_not_counted(session_factory):
    polish = {"cleanup_status": None, "cleanup_ms": None, "cleanup_level": None, "app": COMMAND_APP}
    _seed(
        session_factory,
        [
            _row(**polish, polish_status="applied", polish_ms=800),
            _row(**polish, polish_status="timeout", polish_ms=None),
            _row(**{**polish, "llm_model": None}, polish_status="extension", polish_ms=None),
        ],
    )

    [row] = get_model_speed(session_factory)

    assert (row["level"], row["count"], row["median_ms"], row["timeout_share"]) == ("polish", 2, 800, 0.5)


def test_polish_speed_is_not_split_by_the_transcription_device(session_factory):
    polish = {"cleanup_status": None, "cleanup_ms": None, "cleanup_level": None, "app": COMMAND_APP}
    _seed(
        session_factory,
        [
            _row(**polish, device=None, polish_status="applied", polish_ms=400),  # a quick tap: no recording
            _row(**polish, device="gpu", polish_status="applied", polish_ms=800),
            _row(**polish, device="cpu", polish_status="applied", polish_ms=1200),
        ],
    )

    [row] = get_model_speed(session_factory)

    assert (row["level"], row["device"], row["count"], row["median_ms"]) == ("polish", None, 3, 800)


def test_one_model_name_on_two_servers_is_two_rows_without_credentials(session_factory):
    _seed(
        session_factory,
        [
            _row(llm_url="http://localhost:11434", ms=100),
            _row(llm_url="http://me:secret@gpubox:8080/v1?key=abc", ms=400),
            _row(llm_url="http://me:secret@gpubox:8080/v1?key=abc", ms=600),
        ],
    )

    rows = get_model_speed(session_factory)

    assert [(r["server"], r["count"], r["median_ms"]) for r in rows] == [
        ("gpubox:8080", 2, 500),
        ("localhost:11434", 1, 100),
    ]
    assert "secret" not in str(rows)


def test_a_cleanup_skipped_while_the_model_reloads_is_no_pass(monkeypatch):
    from nexusvox.dictation import cleanup as module

    monkeypatch.setattr(module, "reload_running", lambda llm: True)
    called = []
    monkeypatch.setattr(module, "complete", lambda *a: called.append(a))

    result = cleanup("Ähm ich komme äh heute später", CleanupConfig(level="light"), LlmConfig())

    assert called == []
    assert (result.status, result.llm_model, result.llm_url, result.level) == ("failed", None, None, None)


def test_only_timeouts_leave_no_median(session_factory):
    _seed(session_factory, [_row(status="timeout", ms=None)])

    [row] = get_model_speed(session_factory)

    assert (row["median_ms"], row["timeout_share"]) == (None, 1.0)


def test_the_table_follows_the_date_range(session_factory):
    old = datetime(2020, 1, 1, 12, 0)
    _seed(session_factory, [_row(created_at=old), _row()])

    assert get_model_speed(session_factory, start="2020-01-01", end="2020-01-02")[0]["count"] == 1
    assert get_model_speed(session_factory)[0]["count"] == 2


def test_the_endpoint_serves_the_table(flask_client, db):
    db.save_transcription(
        "hello",
        "en",
        500,
        cleanup_status="applied",
        cleanup_ms=120,
        llm_model="gemma",
        cleanup_level="light",
        transcribe_device="gpu",
    )

    resp = flask_client.get("/api/model-speed")

    assert resp.status_code == 200
    assert resp.get_json() == [
        {
            "model": "gemma",
            "server": None,
            "level": "light",
            "device": "gpu",
            "count": 1,
            "median_ms": 120,
            "timeout_share": 0.0,
        }
    ]
