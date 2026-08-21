"""Shared test fixtures for NexusVox test suite."""

from __future__ import annotations

import io
import wave

import pytest

from nexusvox.config import Config, DatabaseConfig
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.db import Database


@pytest.fixture
def db():
    """Fresh in-memory SQLite database for each test."""
    config = DatabaseConfig(path=":memory:")
    return Database(config)


@pytest.fixture
def session_factory(db):
    """SQLAlchemy session factory from the in-memory database."""
    return db._session_factory


@pytest.fixture
def sample_transcriptions(db):
    """Seed the database with 8 varied transcription records."""
    records = []

    data = [
        ("Hello world this is a test", "en", 2500, None),
        ("Das ist ein deutscher Test", "de", 3200, None),
        ("The quick brown fox jumps over the lazy dog", "en", 4100, 0.92),
        ("Testing speech to text accuracy", "en", 1800, 0.87),
        ("Noch ein deutscher Satz zum Testen", "de", 2900, None),
        ("Multiple words repeated words for testing words", "en", 3500, None),
        ("Short test", "en", 800, 0.95),
        ("Langer deutscher Satz mit vielen Woertern zum Testen der Erkennung", "de", 5200, None),
    ]

    for text, lang, duration, confidence in data:
        record = db.save_transcription(text, lang, duration, confidence)
        records.append(record)

    # Flag one record and add a correction to another
    db.flag_transcription(records[1].id)
    db.flag_transcription(records[5].id)
    db.update_correction(records[5].id, "Multiple words repeated words for testing purposes")

    return records


@pytest.fixture
def flask_client(db, session_factory, monkeypatch):
    """Flask test client backed by the in-memory database."""
    config = Config()
    api = DashboardAPI(session_factory, config, db=db)
    app = _create_app(api)
    app.config["TESTING"] = True

    # Prevent save_config from writing to real config file
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)
    # Make model-listing / switching tests deterministic across GPU-less CI.
    monkeypatch.setattr(
        "nexusvox.dashboard.api.resolve_device",
        lambda d: "cuda" if d in ("auto", "cuda") else "cpu",
    )

    with app.test_client() as client:
        yield client


@pytest.fixture
def sample_wav_bytes():
    """Generate a minimal valid 16kHz mono PCM16 WAV (0.5s of silence)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16_000)
        wf.writeframes(b"\x00\x00" * 8_000)  # 0.5s
    return buf.getvalue()
