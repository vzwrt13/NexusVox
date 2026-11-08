"""Tests for Flask dashboard API endpoints."""

from __future__ import annotations

import json

import pytest

from nexusvox.storage.vocabulary import DictionaryEntry


@pytest.mark.parametrize("body", ["[]", "null", "1", '"text"'])
@pytest.mark.parametrize(
    "url",
    [
        "/api/voice-commands/enabled",  # through _switch
        "/api/settings/log-full-text",
        "/api/settings/llm-pull",  # reads the body inline
        "/api/models/switch",
        "/api/dictionary",
        "/api/review/1",
    ],
)
def test_a_json_body_that_is_no_object_is_a_400(flask_client, url, body):
    resp = flask_client.post(url, data=body, content_type="application/json")

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Expected a JSON object"}


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
    assert "language" not in data
    assert "auto_language_detection" not in data
    assert "translate_enabled" in data
    assert data["translator_url"].startswith("http")


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


@pytest.mark.parametrize("path", ["/api/settings/language", "/api/settings/auto-language-detection"])
def test_language_setting_endpoints_are_gone(flask_client, path):
    resp = flask_client.post(path, data=json.dumps({"language": "de"}), content_type="application/json")

    assert resp.status_code in (404, 405)  # no such route any more


def test_set_translate_enabled(flask_client):
    resp = flask_client.post(
        "/api/settings/translate",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["translate_enabled"] is True


def test_translator_status_reports_unreachable_server(flask_client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api_settings.is_reachable", lambda cfg: False)

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
        "diarization_fallback": True,  # uploads run on Parakeet for speakers
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
    monkeypatch.setattr("nexusvox.dashboard.api_models.resolve_device", lambda d: "cpu")
    monkeypatch.setattr(
        "nexusvox.dashboard.api_models.model_download_state",
        lambda model_id, cache_dir=None: {"downloaded": model_id == "parakeet-tdt-0.6b", "size": "~1 GB"},
    )

    models = {m["id"]: m for m in flask_client.get("/api/models").get_json()}

    assert models["parakeet-tdt-0.6b"]["downloaded"] is True
    assert models["whisper-large-v3-turbo"]["downloaded"] is False
    assert models["whisper-large-v3-turbo"]["size"] == "~1 GB"


def test_get_models_gives_no_mark_for_models_that_run_in_a_container(flask_client, monkeypatch):
    """On a GPU the model lives in the container's own volume, which the local cache says nothing about."""
    monkeypatch.setattr("nexusvox.dashboard.api_models.resolve_device", lambda d: "cuda")
    monkeypatch.setattr(
        "nexusvox.dashboard.api_models.model_download_state", lambda *a, **k: pytest.fail("the local cache was probed")
    )

    models = {m["id"]: m for m in flask_client.get("/api/models").get_json()}

    assert models["parakeet-tdt-0.6b"]["downloaded"] is None
    assert models["whisper-large-v3-turbo"]["downloaded"] is None


def test_the_download_probe_is_reused_until_a_model_finishes_loading(db, session_factory, monkeypatch):
    from nexusvox.config import Config
    from nexusvox.dashboard.api import DashboardAPI

    calls = []

    def probe(model_id, cache_dir=None):
        calls.append(model_id)
        return {"downloaded": False, "size": ""}

    monkeypatch.setattr("nexusvox.dashboard.api_models.resolve_device", lambda d: "cpu")
    monkeypatch.setattr("nexusvox.dashboard.api_models.model_download_state", probe)
    status = {"status": "loading"}
    api = DashboardAPI(session_factory, Config(), db=db, get_switch_status=lambda: status)

    api.get_available_models()
    api.get_available_models()
    assert len(calls) == 2  # one per in-process model, not per request

    api.get_model_status()
    api.get_available_models()
    assert len(calls) == 2  # still loading: the answer stands

    status["status"] = "ready"
    api.get_model_status()
    api.get_available_models()
    assert len(calls) == 4  # ready: probed again


def test_get_models_lists_the_same_models_on_cpu(db, session_factory, monkeypatch):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.inference.device = "cpu"
    monkeypatch.setattr("nexusvox.dashboard.api_models.resolve_device", lambda d: "cpu")

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


def test_get_device(flask_client, monkeypatch, patch_api):
    patch_api("cuda_available", lambda: True)
    monkeypatch.setattr(
        "nexusvox.dashboard.api_models.resolve_device", lambda d: "cuda" if d in ("auto", "cuda") else "cpu"
    )

    resp = flask_client.get("/api/device")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["requested"] == "auto"
    assert data["resolved"] == "cuda"
    assert data["cuda_available"] is True


def test_set_device_persists(flask_client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api_models.resolve_device", lambda d: d if d in ("cuda", "cpu") else "cpu")

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
    from nexusvox.transcription.transcriber import TranscriptionResult

    mock_result = TranscriptionResult()
    mock_result.finalize(text="transcribed text")

    async def fake_transcribe(wav, cfg, **kwargs):
        return mock_result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    job = upload_file(flask_client, sample_wav_bytes)

    assert job["state"] == "done" and job["percent"] == 1.0, job["error"]
    data = job["result"]
    assert data["ok"] is True
    assert data["text"] == "transcribed text"
    assert data["original_filename"] == "test.wav"


def test_file_transcribe_rejects_an_unsupported_format_at_once(flask_client):
    import io

    resp = flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(b"x"), "notes.txt")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400 and "Unsupported format" in resp.get_json()["error"]
    assert flask_client.get("/api/file-transcribe/current").get_json() == {"job": None}


def test_file_job_moves_through_its_stages_to_done(flask_client, sample_wav_bytes, monkeypatch):
    import io
    import threading
    import time

    from nexusvox.transcription.transcriber import TranscriptionResult

    reached = threading.Event()
    release = threading.Event()
    result = TranscriptionResult()
    result.finalize(text="long file")

    async def fake_transcribe(wav_chunks, cfg, *, on_progress=None, **kwargs):
        on_progress("transcribing", 2, 5)
        reached.set()
        assert release.wait(5)
        on_progress("diarizing")
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    resp = flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(sample_wav_bytes), "long.wav")},
        content_type="multipart/form-data",
    )
    job_id = resp.get_json()["job_id"]
    assert reached.wait(5)

    running = flask_client.get(f"/api/file-transcribe/{job_id}").get_json()
    assert running["state"] == "running" and running["stage"] == "transcribing"
    assert (running["current"], running["total"]) == (2, 5)
    assert running["message"] == "Transcribing chunk 2 of 5"
    assert 0.1 < running["percent"] < 0.9 and running["result"] is None
    # A page reopened now finds the same job.
    assert flask_client.get("/api/file-transcribe/current").get_json()["job"]["job_id"] == job_id

    release.set()
    deadline = time.monotonic() + 5
    while flask_client.get(f"/api/file-transcribe/{job_id}").get_json()["state"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    done = flask_client.get(f"/api/file-transcribe/{job_id}").get_json()
    assert done["state"] == "done" and done["result"]["text"] == "long file"


def test_file_job_reports_a_transcription_error(flask_client, db, sample_wav_bytes, monkeypatch):
    async def broken(wav_chunks, cfg, **kwargs):
        raise RuntimeError("server went away")

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", broken)

    job = upload_file(flask_client, sample_wav_bytes)

    assert job["state"] == "error" and job["result"] is None
    assert job["error"] == "Transcription failed: server went away"
    assert job["message"] == job["error"]
    assert db.get_file_transcriptions(limit=1) == []


def test_file_job_reports_a_conversion_error(flask_client):
    job = upload_file(flask_client, b"this is not audio", "broken.mp3")

    assert job["state"] == "error" and job["error"].startswith("Audio conversion failed")


def test_file_job_ends_as_an_error_when_saving_crashes(flask_client, db, sample_wav_bytes, monkeypatch):
    from nexusvox.transcription.transcriber import TranscriptionResult

    result = TranscriptionResult()
    result.finalize(text="x")

    async def fake_transcribe(wav, cfg, **kwargs):
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    def crash(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(db, "save_file_transcription", crash)

    job = upload_file(flask_client, sample_wav_bytes)

    assert job["state"] == "error" and job["error"] == "Saving the transcript failed: disk full"


def test_second_upload_is_refused_while_one_runs(flask_client, sample_wav_bytes, monkeypatch):
    import io
    import threading

    from nexusvox.transcription.transcriber import TranscriptionResult

    release = threading.Event()
    result = TranscriptionResult()
    result.finalize(text="x")

    async def slow(wav_chunks, cfg, **kwargs):
        assert release.wait(5)
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", slow)

    def post():
        return flask_client.post(
            "/api/file-transcribe",
            data={"file": (io.BytesIO(sample_wav_bytes), "a.wav")},
            content_type="multipart/form-data",
        )

    first = post()
    second = post()
    release.set()

    assert first.status_code == 202
    assert second.status_code == 409 and "still being transcribed" in second.get_json()["error"]


def test_unknown_file_job_is_a_404(flask_client):
    assert flask_client.get("/api/file-transcribe/nope").status_code == 404


def test_a_job_that_ended_while_away_is_reported_once(flask_client, sample_wav_bytes, monkeypatch):
    import io
    import time

    async def broken(wav_chunks, cfg, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", broken)

    flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(sample_wav_bytes), "a.wav")},
        content_type="multipart/form-data",
    )
    deadline = time.monotonic() + 5
    job = None
    while time.monotonic() < deadline:
        job = flask_client.get("/api/file-transcribe/current").get_json()["job"]
        if job and job["state"] != "running":
            break
        time.sleep(0.01)

    assert job["state"] == "error" and job["error"] == "Transcription failed: boom"
    assert flask_client.get("/api/file-transcribe/current").get_json() == {"job": None}


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


def test_get_os_commands(experimental_client):
    resp = experimental_client.get("/api/os-commands")

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


def test_set_os_commands_enabled(experimental_client):
    resp = experimental_client.post(
        "/api/os-commands/enabled",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True

    # Verify it persists on subsequent GET
    resp = experimental_client.get("/api/os-commands")
    assert resp.get_json()["enabled"] is True


# ── Review endpoints ────────────────────────────────────────────────


def test_review_endpoint_get(flask_client):
    resp = flask_client.get("/api/review")

    assert resp.status_code == 200
    assert resp.get_json() == {"items": [], "remaining": 0}


def test_review_endpoint_get_with_data(flask_client, db):
    db.save_transcription("test audio", "en", 1000, audio_path="audio/1.wav")

    resp = flask_client.get("/api/review")
    data = resp.get_json()["items"]
    assert len(data) == 1
    assert data[0]["text"] == "test audio"
    assert data[0]["audio_path"] == "audio/1.wav"


def test_review_endpoint_before_id_pages_older(flask_client, db):
    older = db.save_transcription("older", "en", 1000, audio_path="audio/1.wav")
    newer = db.save_transcription("newer", "en", 1000, audio_path="audio/2.wav")

    data = flask_client.get("/api/review").get_json()
    assert [r["id"] for r in data["items"]] == [newer.id, older.id]

    data = flask_client.get(f"/api/review?before_id={newer.id}").get_json()
    assert [r["id"] for r in data["items"]] == [older.id]
    assert data["remaining"] == 0


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
    assert len(resp.get_json()["items"]) == 0


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


def test_set_os_commands_apps(experimental_client):
    apps = {"chrome": "chrome.exe", "terminal": "wt.exe"}
    resp = experimental_client.post(
        "/api/os-commands/apps",
        data=json.dumps({"apps": apps}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["apps"] == apps


def test_set_hotkey_binding(flask_client):
    resp = flask_client.post(
        "/api/settings/hotkey",
        data=json.dumps({"modifiers": ["shift", "ctrl"], "key": "a"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["hotkey_modifiers"] == ["ctrl", "shift"]  # canonical order
    assert data["hotkey_key"] == "A"
    assert "A" in data["hotkey_key_options"] and "F9" in data["hotkey_key_options"]
    settings = flask_client.get("/api/settings").get_json()
    assert settings["hotkey_modifiers"] == ["ctrl", "shift"] and settings["hotkey_key"] == "A"


def test_set_hotkey_binding_rejects_unusable(flask_client):
    for body in ({"modifiers": [], "key": ""}, {"modifiers": ["ctrl"], "key": "enter"}, {"modifiers": "ctrl"}):
        resp = flask_client.post("/api/settings/hotkey", data=json.dumps(body), content_type="application/json")
        assert resp.status_code == 400
        assert "error" in resp.get_json()


def test_set_store_audio(flask_client):
    assert flask_client.get("/api/settings").get_json()["store_audio"] is True
    resp = flask_client.post(
        "/api/settings/store-audio",
        data=json.dumps({"enabled": False}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["store_audio"] is False
    assert flask_client.get("/api/settings").get_json()["store_audio"] is False


def test_get_voice_commands_includes_structural(flask_client):
    data = flask_client.get("/api/voice-commands").get_json()

    assert data["bypass_structural"] is False
    assert "new line" in data["structural"]
    assert all("keyword" in s and "char" in s for s in data["all_structural"])


def test_set_voice_commands_structural(flask_client):
    resp = flask_client.post(
        "/api/voice-commands/structural",
        data=json.dumps({"structural": ["tab"]}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["structural"] == ["tab"]


def test_set_voice_commands_bypass_structural(flask_client):
    resp = flask_client.post(
        "/api/voice-commands/bypass-structural",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["bypass_structural"] is True


def test_learn_from_edits_setting(flask_client):
    assert flask_client.get("/api/settings").get_json()["learn_from_edits"] is False
    resp = flask_client.post(
        "/api/settings/learn-from-edits",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )
    assert resp.get_json()["learn_from_edits"] is True


def test_learn_from_edits_status_without_the_app(flask_client):
    status = flask_client.get("/api/settings/learn-from-edits-status").get_json()
    assert status["counts"] == {} and status["last"] is None
    assert {"suggested", "unchanged", "too_small", "too_many_changes"} <= set(status["watched"])


def test_learn_from_edits_status_from_the_watcher(db, session_factory):
    from nexusvox.config import Config
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI
    from nexusvox.dictation.edit_watch import EditWatcher

    watcher = EditWatcher(lambda s: None, lambda: [])
    watcher.skip("terminal")
    app = _create_app(DashboardAPI(session_factory, Config(), db=db, get_learn_status=watcher.status))
    with app.test_client() as client:
        status = client.get("/api/settings/learn-from-edits-status").get_json()
    assert status["counts"] == {"terminal": 1}
    assert status["last"]["message"] == "skipped: terminals are not watched"


def test_polish_presets_add_edit_default_and_delete(flask_client):
    def post(**body):
        return flask_client.post("/api/polish-presets", data=json.dumps(body), content_type="application/json")

    assert flask_client.get("/api/polish-presets").get_json() == {"presets": []}
    first = post(name="prompt engineer", instruction="Rewrite it as a prompt.", is_default=True).get_json()["preset"]
    second = post(name="kürzer", instruction="Mach es kürzer.", is_default=True).get_json()["preset"]
    presets = flask_client.get("/api/polish-presets").get_json()["presets"]
    assert [(p["name"], p["is_default"]) for p in presets] == [("kürzer", True), ("prompt engineer", False)]

    clash = post(name="Kürzer", instruction="other")
    assert clash.status_code == 400 and "unique" in clash.get_json()["error"]
    edited = post(id=first["id"], name="prompt engineer", instruction="New.", is_default=False).get_json()
    assert edited["preset"]["instruction"] == "New."
    assert flask_client.delete(f"/api/polish-presets/{second['id']}").get_json() == {"ok": True}


def test_cleanup_level_setting(flask_client, monkeypatch):
    import nexusvox.dashboard.api_settings as api_module

    warmed = []
    monkeypatch.setattr(api_module, "llm_warm_up", warmed.append)
    monkeypatch.setattr(api_module, "llm_runs_fast", lambda: True)
    settings = flask_client.get("/api/settings").get_json()
    assert settings["llm_slow_hint"] is False
    assert (settings["cleanup_level"], settings["cleanup_levels"]) == ("off", ["off", "light", "medium"])

    def post(level):
        return flask_client.post(
            "/api/settings/cleanup-level", data=json.dumps({"level": level}), content_type="application/json"
        )

    assert post("light").get_json()["cleanup_level"] == "light"
    assert len(warmed) == 1  # switching cleanup on loads the model
    bad = post("heavy")
    assert bad.status_code == 400 and bad.get_json()["cleanup_level"] == "light"
    flask_client.post(
        "/api/settings/llm-model", data=json.dumps({"model": "qwen3.5:4b"}), content_type="application/json"
    )
    assert len(warmed) == 2  # a new model is loaded while cleanup is on


def test_cleanup_level_can_be_switched_on_without_cuda_and_shows_the_hint(flask_client, monkeypatch):
    import nexusvox.dashboard.api_settings as api_module

    monkeypatch.setattr(api_module, "llm_warm_up", lambda llm: None)
    monkeypatch.setattr(api_module, "llm_runs_fast", lambda: False)
    assert flask_client.get("/api/settings").get_json()["llm_slow_hint"] is True
    resp = flask_client.post(
        "/api/settings/cleanup-level", data=json.dumps({"level": "light"}), content_type="application/json"
    )
    assert resp.status_code == 200 and resp.get_json()["cleanup_level"] == "light"
    assert resp.get_json()["llm_slow_hint"] is True
    monkeypatch.setattr(api_module, "llm_runs_fast", lambda: True)
    assert flask_client.get("/api/settings").get_json()["llm_slow_hint"] is False


def test_polish_in_terminals_setting(flask_client):
    assert flask_client.get("/api/settings").get_json()["polish_in_terminals"] is False
    resp = flask_client.post(
        "/api/settings/polish-terminals",
        data=json.dumps({"enabled": True}),
        content_type="application/json",
    )
    assert resp.get_json()["polish_in_terminals"] is True


def test_dictionary_suggestions_accept_and_dismiss(flask_client, db):
    db.add_dictionary_suggestions(
        [DictionaryEntry(wrong="nexus fox", right="NexusVox"), DictionaryEntry(wrong="Maya", right="Meier")]
    )
    suggestions = flask_client.get("/api/dictionary/suggestions").get_json()["suggestions"]
    by_wrong = {s["wrong"]: s["id"] for s in suggestions}

    accepted = flask_client.post(f"/api/dictionary/suggestions/{by_wrong['nexus fox']}/accept").get_json()
    dismissed = flask_client.delete(f"/api/dictionary/suggestions/{by_wrong['Maya']}").get_json()

    assert accepted["ok"] is True and accepted["entry"]["right"] == "NexusVox"
    assert dismissed["ok"] is True
    assert flask_client.get("/api/dictionary/suggestions").get_json()["suggestions"] == []
    assert [e["wrong"] for e in flask_client.get("/api/dictionary").get_json()["entries"]] == ["nexus fox"]


def test_accept_missing_dictionary_suggestion(flask_client):
    assert flask_client.post("/api/dictionary/suggestions/999/accept").get_json()["ok"] is False


def test_retry_transcribes_the_stored_audio_again(flask_client, db, sample_wav_bytes, tmp_path, monkeypatch):
    from nexusvox.dashboard.api import DashboardAPI
    from nexusvox.transcription.transcriber import TranscriptionResult

    wav = tmp_path / "1.wav"
    wav.write_bytes(sample_wav_bytes)
    record = db.save_transcription("nexus fox", "en", 500, audio_path=str(wav))
    monkeypatch.setattr(DashboardAPI, "get_audio_file_path", lambda self, tid: str(wav))
    result = TranscriptionResult()
    result.finalize(text="NexusVox")
    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", lambda wav, cfg, **kwargs: result)
    monkeypatch.setattr("nexusvox.dashboard.api_files.asyncio.run", lambda coro: coro)

    data = flask_client.post(f"/api/review/{record.id}/retry").get_json()

    assert data["ok"] is True and data["text"] == "NexusVox"
    assert db.get_transcription_text(record.id) == "nexus fox"  # nothing saved


def test_retry_does_not_ask_for_words_and_reports_an_incomplete_run(
    flask_client, db, sample_wav_bytes, tmp_path, monkeypatch
):
    from nexusvox.dashboard.api import DashboardAPI
    from nexusvox.transcription.transcriber import TranscriptionResult

    wav = tmp_path / "1.wav"
    wav.write_bytes(sample_wav_bytes)
    record = db.save_transcription("nexus fox", "en", 500, audio_path=str(wav))
    monkeypatch.setattr(DashboardAPI, "get_audio_file_path", lambda self, tid: str(wav))
    result = TranscriptionResult()
    result.finalize(text="NexusVox")
    result.notes = ["Chunk 1 of 1 timed out; its text may be incomplete or missing."]
    seen: dict = {}

    def fake_transcribe(wav_chunks, cfg, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)
    monkeypatch.setattr("nexusvox.dashboard.api_files.asyncio.run", lambda coro: coro)

    data = flask_client.post(f"/api/review/{record.id}/retry").get_json()

    assert seen["word_timestamps"] is False
    assert data["ok"] is False and "timed out" in data["error"]


def test_upload_asks_for_words_stores_them_and_returns_notes(flask_client, db, sample_wav_bytes, monkeypatch):
    import json

    from nexusvox.transcription.transcriber import TranscriptionResult, Word

    result = TranscriptionResult()
    result.finalize(text="hello world")
    result.words = [Word("hello", 0.1, 0.4), Word("world", 0.5, 0.9)]
    result.notes = ["Chunk 2 of 2 came back without word timestamps."]
    seen: dict = {}

    async def fake_transcribe(wav_chunks, cfg, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    data = upload_file(flask_client, sample_wav_bytes)["result"]

    assert seen["word_timestamps"] is True
    assert seen["diarize"] is False
    assert data["ok"] is True and data["notes"] == result.notes
    stored = db.get_file_transcriptions(limit=1)[0]
    assert [w["word"] for w in json.loads(stored.words)] == ["hello", "world"]
    assert stored.processing_ms is not None and stored.processing_ms >= 0


def test_retry_without_stored_audio_says_why(flask_client, db):
    record = db.save_transcription("no audio", "en", 500)

    data = flask_client.post(f"/api/review/{record.id}/retry").get_json()

    assert data["ok"] is False and "Store Audio Recordings" in data["error"]


def test_paste_key_setting(flask_client):
    assert flask_client.get("/api/settings").get_json()["paste_key"] == "V"
    ok = flask_client.post("/api/settings/paste-key", data=json.dumps({"key": "f8"}), content_type="application/json")
    off = flask_client.post("/api/settings/paste-key", data=json.dumps({"key": ""}), content_type="application/json")
    bad = flask_client.post(
        "/api/settings/paste-key", data=json.dumps({"key": "nope"}), content_type="application/json"
    )

    assert ok.get_json()["paste_key"] == "F8"
    assert off.get_json()["paste_key"] == ""
    assert bad.status_code == 400 and bad.get_json()["paste_key"] == ""


def test_paste_key_rejects_keys_the_listener_would_ignore(flask_client):
    flask_client.post(
        "/api/settings/hotkey",
        data=json.dumps({"modifiers": ["ctrl", "alt"], "key": "B"}),
        content_type="application/json",
    )
    clash = flask_client.post("/api/settings/paste-key", data=json.dumps({"key": "B"}), content_type="application/json")

    assert clash.status_code == 400 and "hotkey" in clash.get_json()["error"]
    assert clash.get_json()["paste_key"] == "V"


def test_polish_key_and_model_settings(flask_client, monkeypatch):
    import nexusvox.dashboard.api_settings as api_module

    def post(path, payload):
        return flask_client.post(path, data=json.dumps(payload), content_type="application/json")

    assert flask_client.get("/api/settings").get_json()["polish_key"] == ""
    assert post("/api/settings/polish-key", {"key": "p"}).get_json()["polish_key"] == "P"
    clash = post("/api/settings/paste-key", {"key": "P"})
    assert clash.status_code == 400 and "polish" in clash.get_json()["error"]
    assert post("/api/settings/llm-model", {"model": "gemma4:12b"}).get_json()["llm_model"] == "gemma4:12b"
    assert post("/api/settings/llm-model", {"model": " "}).status_code == 400

    monkeypatch.setattr(api_module, "llm_model_status", lambda cfg: {"state": "down"})
    status = flask_client.get("/api/settings/llm-status").get_json()
    assert status == {
        "url": "http://127.0.0.1:11434",
        "protocol": "ollama",
        "model": "gemma4:12b",
        "reachable": False,
        "model_installed": None,
    }
    monkeypatch.setattr(api_module, "llm_model_status", lambda cfg: {"state": "missing"})
    status = flask_client.get("/api/settings/llm-status").get_json()
    assert (status["reachable"], status["model_installed"]) == (True, False)


def test_upload_with_diarize_passes_the_flag_and_stores_speakers(flask_client, db, sample_wav_bytes, monkeypatch):
    import json

    from nexusvox.transcription.transcriber import TranscriptionResult, Word

    result = TranscriptionResult()
    result.finalize(text="Speaker 1: hello\n\nSpeaker 2: world")
    result.words = [Word("hello", 0.1, 0.4, speaker=1), Word("world", 0.5, 0.9, speaker=2)]
    seen: dict = {}

    async def fake_transcribe(wav_chunks, cfg, **kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    data = upload_file(flask_client, sample_wav_bytes, "meeting.wav", diarize="1")["result"]

    assert seen["diarize"] is True
    assert data["ok"] is True and data["text"] == "Speaker 1: hello\n\nSpeaker 2: world"
    stored = db.get_file_transcriptions(limit=1)[0]
    assert stored.text == data["text"]
    assert [w["speaker"] for w in json.loads(stored.words)] == [1, 2]
    assert flask_client.get("/api/file-transcriptions").get_json()[0]["text"] == data["text"]


def test_upload_whose_fallback_failed_shows_only_the_container_notice(flask_client, sample_wav_bytes, monkeypatch):
    from contextlib import contextmanager

    from nexusvox.config import InferenceConfig
    from nexusvox.transcription.speaker_fallback import SpeakerJob
    from nexusvox.transcription.transcriber import TranscriptionResult

    notice = "Speaker detection needs the Parakeet GPU container (it did not become healthy); no labels."
    seen: dict = {}

    @contextmanager
    def failed_fallback(config, **kwargs):
        yield SpeakerJob(InferenceConfig(model="custom-server"), notice, failed=True)

    async def fake_transcribe(wav_chunks, cfg, **kwargs):
        seen.update(kwargs)
        result = TranscriptionResult()
        result.finalize(text="hello")
        # What the real function adds unless told the caller explains it.
        result.notes = [] if not kwargs["explain_no_speakers"] else ["Speaker detection needs Parakeet TDT 0.6B"]
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.speaker_detection_job", failed_fallback)
    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    data = upload_file(flask_client, sample_wav_bytes, "meeting.wav", diarize="1")["result"]

    assert seen["explain_no_speakers"] is False
    assert data["notes"] == [notice]


@pytest.mark.parametrize("asked", ["1", ""])
def test_every_upload_goes_through_the_speaker_fallback_decision(
    flask_client, sample_wav_bytes, monkeypatch, caplog, asked
):
    """The one upload path asks `speaker_detection_job` whether speakers are wanted; without them it says so."""
    import logging
    from contextlib import contextmanager

    from nexusvox.transcription.speaker_fallback import SpeakerJob
    from nexusvox.transcription.transcriber import TranscriptionResult

    wanted: list[bool] = []

    @contextmanager
    def spy(config, *, wanted: bool, **kwargs):
        spy.calls.append(wanted)
        yield SpeakerJob(config)

    spy.calls = wanted

    async def fake_transcribe(wav_chunks, cfg, **kwargs):
        result = TranscriptionResult()
        result.finalize(text="hello")
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.speaker_detection_job", spy)
    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)

    with caplog.at_level(logging.INFO):
        data = upload_file(flask_client, sample_wav_bytes, "meeting.wav", **({"diarize": asked} if asked else {}))
    assert data["result"]["ok"] is True

    assert wanted == [bool(asked)]
    assert ("Speaker detection was not requested" in caplog.text) is (not asked)


def _fake_transcription(monkeypatch, text):
    from nexusvox.transcription.transcriber import TranscriptionResult, Word

    result = TranscriptionResult()
    result.finalize(text=text)
    result.words = [Word("well", 0.1, 0.4)]

    async def fake_transcribe(wav_chunks, cfg, **kwargs):
        return result

    monkeypatch.setattr("nexusvox.dashboard.api_files.transcribe_file", fake_transcribe)


def test_upload_with_cleanup_stores_the_cleaned_text_beside_the_original(
    flask_client, db, sample_wav_bytes, monkeypatch
):
    import json

    _fake_transcription(monkeypatch, "Speaker 1: well um hello there.\n\nSpeaker 2: um hi.")
    monkeypatch.setattr(
        "nexusvox.dictation.cleanup.complete",
        lambda config, system, user: "Well, hello there." if "hello" in user else "Hi.",
    )

    job = upload_file(flask_client, sample_wav_bytes, cleanup="light")

    data = job["result"]
    assert job["state"] == "done", job["error"]
    stored = db.get_file_transcriptions(limit=1)[0]
    assert stored.text == "Speaker 1: well um hello there.\n\nSpeaker 2: um hi."  # the transcript is untouched
    assert stored.cleaned_text == "Speaker 1: Well, hello there.\n\nSpeaker 2: Hi."
    assert stored.cleanup_level == "light"
    assert json.loads(stored.words) == [{"word": "well", "start": 0.1, "end": 0.4}]
    assert data["text"] == stored.text and data["cleaned_text"] == stored.cleaned_text
    listed = flask_client.get("/api/file-transcriptions").get_json()[0]
    assert (listed["text"], listed["cleaned_text"]) == (stored.text, stored.cleaned_text)


def test_upload_with_failing_cleanup_still_completes_with_a_note(flask_client, db, sample_wav_bytes, monkeypatch):
    from nexusvox.llm import LlmError

    _fake_transcription(monkeypatch, "well um this is a longer sentence than before")

    def down(config, system, user):
        raise LlmError("connection refused")

    monkeypatch.setattr("nexusvox.dictation.cleanup.complete", down)

    job = upload_file(flask_client, sample_wav_bytes, cleanup="medium")

    assert job["state"] == "done"
    assert any("Text cleanup" in note for note in job["result"]["notes"])
    stored = db.get_file_transcriptions(limit=1)[0]
    assert stored.text == "well um this is a longer sentence than before"
    assert stored.cleaned_text is None and stored.cleanup_level is None


@pytest.mark.parametrize("value", ["", "off", "heavy"])
def test_upload_without_a_valid_cleanup_level_asks_no_model(flask_client, db, sample_wav_bytes, monkeypatch, value):
    _fake_transcription(monkeypatch, "well um this is a longer sentence than before")

    def fail(*args):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr("nexusvox.dictation.cleanup.complete", fail)

    job = upload_file(flask_client, sample_wav_bytes, cleanup=value)

    assert job["state"] == "done"
    assert job["result"]["cleaned_text"] is None


def test_feedback_mode_setting(flask_client):
    settings = flask_client.get("/api/settings").get_json()
    assert (settings["feedback_mode"], settings["feedback_modes"]) == ("sound", ["sound", "visual", "both"])

    def post(mode):
        return flask_client.post(
            "/api/settings/feedback-mode", data=json.dumps({"mode": mode}), content_type="application/json"
        )

    assert post("both").get_json()["feedback_mode"] == "both"
    bad = post("flashy")
    assert bad.status_code == 400 and bad.get_json()["feedback_mode"] == "both"


def test_llm_models_endpoint_reports_the_loaded_split(flask_client, monkeypatch):
    from nexusvox.dashboard import api_settings as api_module

    monkeypatch.setattr(
        api_module, "llm_build_model_list", lambda cfg, cuda, vram: {"reachable": True, "recommended": [], "other": []}
    )
    monkeypatch.setattr(api_module, "llm_loaded_model", lambda cfg, name, disk_gb=None: {"vram_gb": 5.0, "ram_gb": 2.0})
    assert flask_client.get("/api/settings/llm-models").get_json()["loaded"] == {"vram_gb": 5.0, "ram_gb": 2.0}
    monkeypatch.setattr(api_module, "llm_loaded_model", lambda cfg, name, disk_gb=None: None)
    assert flask_client.get("/api/settings/llm-models").get_json()["loaded"] is None


@pytest.mark.parametrize(
    ("marker_route", "marker_key", "label"),
    [(None, "V", "paste key"), ("/api/settings/polish-key", "P", "polish key")],
)
def test_hotkey_cannot_take_the_key_of_a_marker(flask_client, marker_route, marker_key, label):
    if marker_route:
        assert flask_client.post(marker_route, json={"key": marker_key}).status_code == 200

    resp = flask_client.post("/api/settings/hotkey", json={"modifiers": ["ctrl", "win"], "key": marker_key})

    assert resp.status_code == 400 and label in resp.get_json()["error"]
    assert flask_client.get("/api/settings").get_json()["hotkey_key"] == ""


def test_hotkey_cannot_take_the_os_control_command_key(session_factory, db, no_config_save):
    from nexusvox.config import Config
    from nexusvox.dashboard.api import DashboardAPI

    config = Config()
    config.os_control.enabled = True
    config.os_control.command_key = "Y"
    api = DashboardAPI(session_factory, config, db=db)

    assert "os-control command key" in api.set_hotkey(["ctrl", "win"], "Y")["error"]
    config.os_control.enabled = False
    assert "error" not in api.set_hotkey(["ctrl", "win"], "Y")


def test_hotkey_may_use_a_key_no_marker_has(flask_client):
    resp = flask_client.post("/api/settings/hotkey", json={"modifiers": ["ctrl", "win"], "key": "F9"})

    assert resp.status_code == 200 and resp.get_json()["hotkey_key"] == "F9"


SWITCHES = [
    "/api/settings/learn-from-edits",
    "/api/settings/polish-terminals",
    "/api/settings/mic-guard",
    "/api/settings/store-audio",
    "/api/settings/meeting-consent-hint",
    "/api/settings/meeting-leave-dictation-out",
    "/api/settings/meeting-store-audio",
    "/api/settings/meeting-live-transcript",
    "/api/settings/translate",
    "/api/voice-commands/enabled",
    "/api/voice-commands/numbers",
    "/api/voice-commands/press-enter",
    "/api/voice-commands/bypass-symbols",
    "/api/voice-commands/bypass-structural",
]


@pytest.mark.parametrize("route", SWITCHES)
@pytest.mark.parametrize("bad", ["false", 0, 1, None, []])
def test_a_switch_refuses_anything_but_a_boolean(flask_client, route, bad):
    before = flask_client.get("/api/settings").get_json()

    resp = flask_client.post(route, json={"enabled": bad})

    assert resp.status_code == 400 and resp.get_json()["error"] == "enabled must be true or false"
    assert flask_client.get("/api/settings").get_json() == before


@pytest.mark.parametrize("route", SWITCHES)
def test_a_switch_takes_a_boolean_and_keeps_its_default_when_none_is_sent(flask_client, route):
    assert flask_client.post(route, json={"enabled": True}).status_code == 200
    assert flask_client.post(route, json={"enabled": False}).status_code == 200
    assert flask_client.post(route, json={}).status_code == 200


def test_the_os_commands_switch_refuses_anything_but_a_boolean(experimental_client):
    resp = experimental_client.post("/api/os-commands/enabled", json={"enabled": "false"})

    assert resp.status_code == 400
    assert experimental_client.get("/api/os-commands").get_json()["enabled"] is False
