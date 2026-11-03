"""Tests for Flask dashboard API endpoints."""

from __future__ import annotations

import json

from nexusvox.dictionary import DictionaryEntry


def upload_file(client, wav_bytes, name="test.wav", **form):
    """POST a file to /api/file-transcribe, wait for its job to end and return the job's final status."""
    import io
    import time

    resp = client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(wav_bytes), name), **form},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/file-transcribe/{job_id}").get_json()
        if job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("file job did not end")


def test_get_settings(flask_client):
    resp = flask_client.get("/api/settings")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "auto_language_detection" in data
    assert "language" in data
    assert "translate_enabled" in data
    assert data["translator_url"].startswith("http")


def test_set_auto_language_detection(flask_client):
    resp = flask_client.post(
        "/api/settings/auto-language-detection",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["auto_language_detection"] is True


def test_set_language(flask_client):
    resp = flask_client.post(
        "/api/settings/language",
        data=json.dumps({"language": "de"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["language"] == "de"


def test_set_hotkey_mode(flask_client):
    resp = flask_client.post(
        "/api/settings/hotkey-mode",
        data=json.dumps({"mode": "toggle"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["hotkey_mode"] == "toggle"
    assert flask_client.get("/api/settings").get_json()["hotkey_mode"] == "toggle"


def test_set_mic_guard_enabled(flask_client):
    resp = flask_client.post(
        "/api/settings/mic-guard",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["mic_guard_enabled"] is True
    assert flask_client.get("/api/settings").get_json()["mic_guard_enabled"] is True


def test_set_hotkey_mode_rejects_unknown(flask_client):
    resp = flask_client.post(
        "/api/settings/hotkey-mode",
        data=json.dumps({"mode": "double-tap"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_set_language_rejects_unknown_code(flask_client):
    resp = flask_client.post(
        "/api/settings/language",
        data=json.dumps({"language": "xx"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_set_translate_enabled(flask_client):
    resp = flask_client.post(
        "/api/settings/translate",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["translate_enabled"] is True


def test_translator_status_reports_unreachable_server(flask_client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api.is_reachable", lambda cfg: False)

    resp = flask_client.get("/api/settings/translator-status")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reachable"] is False
    assert data["url"].startswith("http")


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


def test_press_enter_off_by_default(flask_client):
    assert flask_client.get("/api/voice-commands").get_json()["press_enter"] is False


def test_set_voice_commands_press_enter(flask_client):
    resp = flask_client.post(
        "/api/voice-commands/press-enter",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["press_enter"] is True


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


def test_flagged_endpoint(experimental_client):
    resp = experimental_client.get("/api/flagged")

    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_flagged_includes_audio_path(experimental_client, db):
    record = db.save_transcription("test audio", "en", 1000, audio_path="audio/1_123.wav")
    db.flag_transcription(record.id)

    resp = experimental_client.get("/api/flagged")
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["audio_path"] == "audio/1_123.wav"


def test_audio_endpoint_not_found(flask_client):
    resp = flask_client.get("/api/audio/9999")
    assert resp.status_code == 404


def test_correct_transcription_endpoint(experimental_client, db):
    record = db.save_transcription("tset", "en", 1000)
    db.flag_transcription(record.id)

    resp = experimental_client.post(
        f"/api/flagged/{record.id}/correct",
        data=json.dumps({"corrected_text": "test"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_latency_trend_endpoint(flask_client):
    resp = flask_client.get("/api/latency-trend?period=week")

    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"labels", "transcribe_ms", "latency_ms"}


def test_model_breakdown_endpoint(flask_client, sample_transcriptions):
    resp = flask_client.get("/api/model-breakdown")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["count"] == len(sample_transcriptions)


def test_app_breakdown_endpoint(flask_client, sample_transcriptions):
    resp = flask_client.get("/api/app-breakdown")

    assert resp.status_code == 200
    data = resp.get_json()
    words = sum(len(t.text.split()) for t in sample_transcriptions)
    assert data == [{"app": "Unknown", "count": len(sample_transcriptions), "words": words}]


def test_accuracy_endpoint(flask_client, db):
    db.save_transcription("hello there world", "en", 1000)
    db.record_learn_event("suggested")
    db.record_dictionary_hits({DictionaryEntry("hello", "Hi"): 2})

    resp = flask_client.get("/api/accuracy?period=month")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["words"] == 3
    assert data["learn"]["suggested"] == 1
    assert data["dictionary"]["total_hits"] == 2
    assert data["most_corrected"] == []


# ── Model endpoints ──────────────────────────────────────────────────


def test_get_models(flask_client):
    resp = flask_client.get("/api/models")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    ids = [m["id"] for m in data]
    assert ids == ["parakeet-tdt-0.6b", "whisper-large-v3-turbo", "custom-server"]
    for m in data:
        assert "name" in m
        assert "protocol" in m


def test_get_current_model(flask_client):
    resp = flask_client.get("/api/models/current")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "model" in data
    assert data["model"] == "parakeet-tdt-0.6b"
    assert data["diarization"] is True
    assert data["diarization_models"] == ["Parakeet TDT 0.6B"]
    assert data["diarization_hint"] == ""


def test_switch_model(flask_client):
    resp = flask_client.post(
        "/api/models/switch",
        data=json.dumps({"model": "whisper-large-v3-turbo"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["model"] == "whisper-large-v3-turbo"

    # Verify current model changed
    resp = flask_client.get("/api/models/current")
    assert resp.get_json() == {
        "model": "whisper-large-v3-turbo",
        "diarization": False,
        "diarization_models": ["Parakeet TDT 0.6B"],
        "diarization_hint": "",
    }


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
    assert all("inprocess_supported" in m for m in data)
    assert all("requires_gpu" not in m for m in data)


def test_get_models_marks_what_is_downloaded(flask_client, monkeypatch):
    monkeypatch.setattr(
        "nexusvox.dashboard.api.model_download_state",
        lambda model_id, cache_dir=None: {"downloaded": model_id == "parakeet-tdt-0.6b", "size": "~1 GB"},
    )

    models = {m["id"]: m for m in flask_client.get("/api/models").get_json()}

    assert models["parakeet-tdt-0.6b"]["downloaded"] is True
    assert models["whisper-large-v3-turbo"]["downloaded"] is False
    assert models["whisper-large-v3-turbo"]["size"] == "~1 GB"


def test_get_models_lists_the_same_models_on_cpu(db, session_factory, monkeypatch):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.inference.device = "cpu"
    monkeypatch.setattr("nexusvox.dashboard.api.resolve_device", lambda d: "cpu")

    app = _create_app(DashboardAPI(session_factory, config, db=db))
    app.config["TESTING"] = True

    with app.test_client() as client:
        ids = [m["id"] for m in client.get("/api/models").get_json()]

    assert ids == ["parakeet-tdt-0.6b", "whisper-large-v3-turbo", "custom-server"]


def test_switch_to_a_removed_model_is_rejected(flask_client):
    resp = flask_client.post(
        "/api/models/switch", data=json.dumps({"model": "voxtral-mini-4b"}), content_type="application/json"
    )

    assert resp.get_json()["ok"] is False
