"""Regression tests: dashboard routes reject wrong types, oversized uploads and paths outside their folder."""

from __future__ import annotations

import io
import json
import tempfile

import pytest

from nexusvox.config import Config
from nexusvox.dashboard import benchmarks
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.transcription.file_transcribe import MAX_UPLOAD_BYTES


def post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


@pytest.mark.parametrize("symbols", [5, "comma", {"a": 1}, ["comma", 3], None])
def test_voice_command_symbols_of_the_wrong_type_are_a_400_and_change_nothing(flask_client, symbols):
    before = flask_client.get("/api/voice-commands").get_json()["symbols"]

    resp = post(flask_client, "/api/voice-commands/symbols", {"symbols": symbols})

    assert resp.status_code == 400
    assert flask_client.get("/api/voice-commands").get_json()["symbols"] == before


@pytest.mark.parametrize("apps", [5, ["notepad"], "notepad", {"notepad": 1}, {"notepad": None}])
def test_os_command_apps_of_the_wrong_type_are_a_400_and_change_nothing(experimental_client, apps):
    before = experimental_client.get("/api/os-commands").get_json()["apps"]

    resp = post(experimental_client, "/api/os-commands/apps", {"apps": apps})

    assert resp.status_code == 400
    assert experimental_client.get("/api/os-commands").get_json()["apps"] == before


@pytest.mark.parametrize("body", [{"is_correct": "false"}, {"is_correct": 0}, {"corrected_text": 5}])
def test_a_review_with_wrong_types_is_a_400(flask_client, db, body):
    record = db.save_transcription("hello", "en", 500)

    resp = post(flask_client, f"/api/review/{record.id}", body)

    assert resp.status_code == 400
    assert db.get_transcription_text(record.id) == "hello"


def test_a_review_with_real_types_is_stored(flask_client, db):
    record = db.save_transcription("hello", "en", 500)

    resp = post(flask_client, f"/api/review/{record.id}", {"is_correct": False, "corrected_text": "hullo"})

    assert resp.status_code == 200 and resp.get_json()["ok"] is True


def test_a_correction_that_is_no_text_is_a_400(experimental_client, db):
    record = db.save_transcription("hello", "en", 500)

    resp = post(experimental_client, f"/api/flagged/{record.id}/correct", {"corrected_text": 5})

    assert resp.status_code == 400


def test_the_audio_of_a_deleted_wav_is_a_404(flask_client, db):
    record = db.save_transcription("hello", "en", 500)
    with db._session_factory() as session:
        session.get(type(record), record.id).audio_path = "audio/gone.wav"
        session.commit()

    assert flask_client.get(f"/api/audio/{record.id}").status_code == 404


def test_a_retry_without_a_database_names_that_cause():
    result = DashboardAPI(lambda: None, Config(), db=None).retry_transcription(1)

    assert result["ok"] is False and "Store Audio" not in result["error"]


def test_an_upload_above_the_limit_is_refused_and_its_temporary_file_removed(flask_client, monkeypatch, tmp_path):
    monkeypatch.setattr("nexusvox.dashboard.api_files.MAX_UPLOAD_BYTES", 1000)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))

    resp = flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(b"\0" * 2000), "big.wav")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"ok": False, "error": "File too large (max 2 GB)"}
    assert list(uploads.iterdir()) == []


def test_an_unsupported_upload_is_refused_before_its_body_is_saved(flask_client, monkeypatch, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))

    resp = flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(b"MZ"), "tool.exe")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400 and resp.get_json()["error"] == "Unsupported format: .exe"
    assert list(uploads.iterdir()) == []


def test_renaming_with_a_body_that_is_no_object_is_a_400(flask_client):
    resp = flask_client.patch("/api/file-transcriptions/1", data="[]", content_type="application/json")

    assert resp.status_code == 400 and resp.get_json() == {"error": "Expected a JSON object"}


def test_a_benchmark_path_outside_the_folder_is_not_served(tmp_path):
    bench_dir = tmp_path / "benchmarks"
    bench_dir.mkdir()
    private = tmp_path / "benchmarks_private"
    private.mkdir()
    (private / "secret.json").write_text('{"summary": {}}', encoding="utf-8")

    assert benchmarks.load_benchmark(bench_dir, "../benchmarks_private/secret.json") is None
    assert benchmarks.load_benchmark(bench_dir, r"..\benchmarks_private\secret.json") is None


def test_a_benchmark_inside_the_folder_is_served(tmp_path):
    (tmp_path / "a.json").write_text('{"summary": {"lang": "en_us"}}', encoding="utf-8")

    assert benchmarks.load_benchmark(tmp_path, "a.json") == {"summary": {"lang": "en_us"}}


def test_odd_benchmark_files_are_skipped_not_a_500(tmp_path):
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "summary_list.json").write_text('{"summary": []}', encoding="utf-8")
    (tmp_path / "null_wer.json").write_text('{"summary": {"lang": "en_us", "wer_mean": null}}', encoding="utf-8")
    (tmp_path / "good.json").write_text('{"summary": {"lang": "en_us", "wer_mean": 0.05}}', encoding="utf-8")

    names = {b["filename"] for b in benchmarks.list_benchmarks(tmp_path)}
    compared = {b["filename"]: b["passes_target"] for b in benchmarks.compare_benchmarks(tmp_path)["benchmarks"]}

    assert names == {"null_wer.json", "good.json"}
    assert compared == {"null_wer.json": None, "good.json": True}


@pytest.mark.parametrize("is_default", ["false", 1, None])
def test_a_preset_default_flag_that_is_no_boolean_is_a_400(flask_client, is_default):
    body = {"name": "Formal", "instruction": "Be formal", "is_default": is_default}

    resp = post(flask_client, "/api/polish-presets", body)

    assert resp.status_code == 400
    assert flask_client.get("/api/polish-presets").get_json() == {"presets": []}


@pytest.mark.parametrize(
    "body", [{"name": None, "instruction": "x"}, {"name": "A", "instruction": ["x"]}, {"id": True}, {"id": "abc"}]
)
def test_a_preset_with_wrong_field_types_is_a_400(flask_client, body):
    resp = post(flask_client, "/api/polish-presets", {"name": "Formal", "instruction": "Be formal", **body})

    assert resp.status_code == 400
    assert flask_client.get("/api/polish-presets").get_json() == {"presets": []}


@pytest.mark.parametrize("structural", ["period", 5, [1], None])
def test_voice_command_structural_of_the_wrong_type_is_a_400_and_changes_nothing(flask_client, structural):
    before = flask_client.get("/api/voice-commands").get_json()["structural"]

    resp = post(flask_client, "/api/voice-commands/structural", {"structural": structural})

    assert resp.status_code == 400
    assert flask_client.get("/api/voice-commands").get_json()["structural"] == before


def test_an_oversized_request_gets_a_json_413_with_the_size_message(flask_client):
    # The declared length is what Werkzeug checks before it reads a byte, so no 2 GB body is built here.
    resp = flask_client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(b"\0" * 10), "big.wav")},
        content_type="multipart/form-data",
        environ_overrides={"CONTENT_LENGTH": str(MAX_UPLOAD_BYTES + 3 * 1024 * 1024)},
    )

    assert resp.status_code == 413
    assert resp.get_json() == {"ok": False, "error": "File too large (max 2 GB)"}


def test_the_upload_limit_is_two_gigabytes():
    assert MAX_UPLOAD_BYTES == 2 * 1024**3


def test_a_benchmark_with_a_non_text_lang_is_skipped(tmp_path):
    (tmp_path / "odd.json").write_text('{"summary": {"lang": ["en_us"], "wer_mean": 0.1}}', encoding="utf-8")

    assert benchmarks.compare_benchmarks(tmp_path)["benchmarks"] == []


def test_a_benchmark_that_is_no_utf8_is_none_not_an_error(tmp_path):
    (tmp_path / "latin.json").write_bytes('{"summary": {"model": "café"}}'.encode("latin-1"))

    assert benchmarks.load_benchmark(tmp_path, "latin.json") is None
    assert benchmarks.list_benchmarks(tmp_path) == []


def test_a_retry_names_a_deleted_wav_not_the_store_audio_setting(flask_client, db):
    record = db.save_transcription("hello", "en", 500)
    with db._session_factory() as session:
        session.get(type(record), record.id).audio_path = "audio/gone.wav"
        session.commit()

    data = flask_client.post(f"/api/review/{record.id}/retry").get_json()

    assert data["ok"] is False and "missing" in data["error"] and "Store Audio" not in data["error"]
