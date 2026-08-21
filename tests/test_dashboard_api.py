"""Tests for Flask dashboard API endpoints."""

from __future__ import annotations

import json


def test_get_settings(flask_client):
    resp = flask_client.get("/api/settings")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "auto_language_detection" in data
    assert "language" in data


def test_set_auto_language_detection(flask_client):
    resp = flask_client.post(
        "/api/settings/auto-language-detection",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["auto_language_detection"] is True


def test_get_voice_commands(flask_client):
    resp = flask_client.get("/api/voice-commands")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "enabled" in data
    assert "numbers_as_digits" in data
    assert "symbols" in data
    assert isinstance(data["all_symbols"], list)
    assert all("keyword" in s and "char" in s and "safe" in s for s in data["all_symbols"])


def test_set_voice_commands_enabled(flask_client):
    resp = flask_client.post(
        "/api/voice-commands/enabled",
        data=json.dumps({"enabled": False}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is False


def test_set_voice_commands_numbers(flask_client):
    resp = flask_client.post(
        "/api/voice-commands/numbers",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["numbers_as_digits"] is True


def test_set_voice_commands_symbols(flask_client):
    resp = flask_client.post(
        "/api/voice-commands/symbols",
        data=json.dumps({"symbols": ["slash", "plus"]}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["symbols"] == ["slash", "plus"]


def test_overview_endpoint(flask_client):
    resp = flask_client.get("/api/overview")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "total_transcriptions" in data
    assert "avg_wpm" in data


def test_overview_with_date_params(flask_client):
    resp = flask_client.get("/api/overview?start=2026-01-01&end=2026-12-31")
    assert resp.status_code == 200


def test_transcriptions_over_time_endpoint(flask_client):
    resp = flask_client.get("/api/transcriptions-over-time?period=day")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "labels" in data
    assert "values" in data


def test_language_distribution_endpoint(flask_client):
    resp = flask_client.get("/api/language-distribution")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "labels" in data
    assert "values" in data


def test_top_words_endpoint(flask_client):
    resp = flask_client.get("/api/top-words?n=5")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "labels" in data


def test_flagged_endpoint(flask_client):
    resp = flask_client.get("/api/flagged")

    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_flagged_includes_audio_path(flask_client, db):
    record = db.save_transcription("test audio", "en", 1000, audio_path="audio/1_123.wav")
    db.flag_transcription(record.id)

    resp = flask_client.get("/api/flagged")
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["audio_path"] == "audio/1_123.wav"


def test_audio_endpoint_not_found(flask_client):
    resp = flask_client.get("/api/audio/9999")
    assert resp.status_code == 404


def test_correct_transcription_endpoint(flask_client, db):
    record = db.save_transcription("tset", "en", 1000)
    db.flag_transcription(record.id)

    resp = flask_client.post(
        f"/api/flagged/{record.id}/correct",
        data=json.dumps({"corrected_text": "test"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_confidence_trend_endpoint(flask_client):
    resp = flask_client.get("/api/confidence-trend?period=week")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "labels" in data
    assert "values" in data


# ── Model endpoints ──────────────────────────────────────────────────


def test_get_models(flask_client):
    resp = flask_client.get("/api/models")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 3
    ids = [m["id"] for m in data]
    assert "voxtral-mini-4b" in ids
    assert "cohere-transcribe" in ids
    assert "parakeet-tdt-0.6b" in ids
    for m in data:
        assert "name" in m
        assert "protocol" in m


def test_get_current_model(flask_client):
    resp = flask_client.get("/api/models/current")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "model" in data
    assert data["model"] == "parakeet-tdt-0.6b"


def test_switch_model(flask_client):
    resp = flask_client.post(
        "/api/models/switch",
        data=json.dumps({"model": "cohere-transcribe"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["model"] == "cohere-transcribe"

    # Verify current model changed
    resp = flask_client.get("/api/models/current")
    assert resp.get_json()["model"] == "cohere-transcribe"


def test_model_status_endpoint(flask_client):
    resp = flask_client.get("/api/models/status")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "idle"


def test_switch_model_unknown(flask_client):
    resp = flask_client.post(
        "/api/models/switch",
        data=json.dumps({"model": "nonexistent"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False


def test_get_models_includes_device_metadata(flask_client):
    resp = flask_client.get("/api/models")
    data = resp.get_json()
    assert all("requires_gpu" in m for m in data)
    assert all("inprocess_supported" in m for m in data)


def test_get_models_filters_gpu_only_when_device_is_cpu(db, session_factory, monkeypatch):
    """When resolved device is CPU, only inprocess-capable models are listed."""
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.inference.device = "cpu"
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: "cpu")

    api = DashboardAPI(session_factory, config, db=db)
    app = _create_app(api)
    app.config["TESTING"] = True
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)

    with app.test_client() as client:
        resp = client.get("/api/models")
        data = resp.get_json()

    ids = {m["id"] for m in data}
    assert "whisper-large-v3-turbo" in ids
    assert "whisper-small" in ids
    assert "distil-whisper-large-v3" in ids
    assert "whisper-medium" in ids
    assert "whisper-base" in ids
    assert "voxtral-mini-4b" not in ids
    assert "cohere-transcribe" not in ids
    assert "parakeet-tdt-0.6b" not in ids


def test_switch_to_gpu_model_on_cpu_is_rejected(db, session_factory, monkeypatch):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.inference.device = "cpu"
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: "cpu")

    api = DashboardAPI(session_factory, config, db=db)
    app = _create_app(api)
    app.config["TESTING"] = True
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)

    with app.test_client() as client:
        resp = client.post(
            "/api/models/switch",
            data=json.dumps({"model": "voxtral-mini-4b"}),
            content_type="application/json",
        )

    data = resp.get_json()
    assert data["ok"] is False
    assert "GPU" in data["error"]


# ── Device endpoints ─────────────────────────────────────────────────


def test_get_device(flask_client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: "cuda" if d in ("auto", "cuda") else "cpu")

    resp = flask_client.get("/api/device")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["requested"] == "auto"
    assert data["resolved"] == "cuda"
    assert data["cuda_available"] is True


def test_set_device_persists(flask_client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: d if d in ("cuda", "cpu") else "cpu")

    resp = flask_client.post(
        "/api/device",
        data=json.dumps({"device": "cpu"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["requires_restart"] is True
    assert data["requested"] == "cpu"

    resp = flask_client.get("/api/device")
    assert resp.get_json()["requested"] == "cpu"


def test_set_device_rejects_invalid_value(flask_client):
    resp = flask_client.post(
        "/api/device",
        data=json.dumps({"device": "quantum"}),
        content_type="application/json",
    )

    data = resp.get_json()
    assert data["ok"] is False
    assert "auto" in data["error"]


# ── File upload endpoints ────────────────────────────────────────────


def test_file_transcribe_no_file(flask_client):
    resp = flask_client.post("/api/file-transcribe")

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False


def test_file_transcribe_success(flask_client, sample_wav_bytes, monkeypatch):
    from nexusvox.transcriber import TranscriptionResult

    mock_result = TranscriptionResult()
    mock_result.finalize(text="transcribed text")

    monkeypatch.setattr(
        "nexusvox.dashboard.api.transcribe_file",
        lambda wav, cfg, **kwargs: mock_result,
    )
    monkeypatch.setattr(
        "nexusvox.dashboard.api.asyncio.run",
        lambda coro: mock_result,
    )

    import io

    resp = flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(sample_wav_bytes), "test.wav")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["text"] == "transcribed text"
    assert data["original_filename"] == "test.wav"


def test_file_transcriptions_list_endpoint(flask_client, db):
    db.save_file_transcription("hello", "en", 2000, "test.wav", model="parakeet-tdt-0.6b")

    resp = flask_client.get("/api/file-transcriptions")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["text"] == "hello"
    assert data[0]["original_filename"] == "test.wav"


def test_file_transcriptions_empty(flask_client):
    resp = flask_client.get("/api/file-transcriptions")

    assert resp.status_code == 200
    assert resp.get_json() == []


# ── OS Commands endpoints ────────────────────────────────────────────


def test_get_os_commands(flask_client):
    resp = flask_client.get("/api/os-commands")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is False
    assert data["apps"] == {}
    assert isinstance(data["supported_actions"], list)
    assert len(data["supported_actions"]) == 8
    actions = [a["action"] for a in data["supported_actions"]]
    assert "open" in actions
    assert "focus" in actions
    assert "snap left" in actions
    assert "flag" in actions


def test_set_os_commands_enabled(flask_client):
    resp = flask_client.post(
        "/api/os-commands/enabled",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True

    # Verify it persists on subsequent GET
    resp = flask_client.get("/api/os-commands")
    assert resp.get_json()["enabled"] is True


# ── Review endpoints ────────────────────────────────────────────────


def test_review_endpoint_get(flask_client):
    resp = flask_client.get("/api/review")

    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_review_endpoint_get_with_data(flask_client, db):
    db.save_transcription("test audio", "en", 1000, audio_path="audio/1.wav")

    resp = flask_client.get("/api/review")
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["text"] == "test audio"
    assert data[0]["audio_path"] == "audio/1.wav"


def test_review_submit_correct(flask_client, db):
    record = db.save_transcription("test", "en", 1000, audio_path="audio/1.wav")

    resp = flask_client.post(
        f"/api/review/{record.id}",
        data=json.dumps({"is_correct": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Should no longer appear in unreviewed
    resp = flask_client.get("/api/review")
    assert len(resp.get_json()) == 0


def test_review_submit_incorrect(flask_client, db):
    record = db.save_transcription("tset", "en", 1000, audio_path="audio/1.wav")

    resp = flask_client.post(
        f"/api/review/{record.id}",
        data=json.dumps({"is_correct": False, "corrected_text": "test"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_review_submit_nonexistent(flask_client):
    resp = flask_client.post(
        "/api/review/9999",
        data=json.dumps({"is_correct": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False


def test_set_os_commands_apps(flask_client):
    apps = {"chrome": "chrome.exe", "terminal": "wt.exe"}
    resp = flask_client.post(
        "/api/os-commands/apps",
        data=json.dumps({"apps": apps}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["apps"] == apps
