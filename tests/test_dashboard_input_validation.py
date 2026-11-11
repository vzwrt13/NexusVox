"""Regression tests: dashboard routes reject wrong types, oversized uploads and paths outside their folder."""

from __future__ import annotations

import io
import json
from pathlib import Path

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


def test_an_oversized_upload_is_refused_without_reading_all_of_it(flask_client):
    big = io.BytesIO(b"\0" * (MAX_UPLOAD_BYTES + 100))

    resp = flask_client.post(
        "/api/file-transcribe", data={"file": (big, "big.wav")}, content_type="multipart/form-data"
    )

    assert resp.status_code in (400, 413)


def test_a_request_body_far_above_the_upload_limit_is_a_413(flask_client):
    huge = io.BytesIO(b"\0" * (MAX_UPLOAD_BYTES + 3 * 1024 * 1024))

    resp = flask_client.post(
        "/api/file-transcribe", data={"file": (huge, "big.wav")}, content_type="multipart/form-data"
    )

    assert resp.status_code == 413


def test_renaming_with_a_body_that_is_no_object_is_a_400(flask_client):
    resp = flask_client.patch("/api/file-transcriptions/1", data="[]", content_type="application/json")

    assert resp.status_code == 400 and resp.get_json() == {"error": "Expected a JSON object"}


def test_a_route_does_not_parse_its_json_body_itself():
    import nexusvox.dashboard as dashboard

    modules = Path(dashboard.__file__).parent.glob("routes_*.py")

    own = [p.name for p in modules if p.stem != "routes_base" and "get_json(" in p.read_text("utf-8")]

    assert own == []


def test_a_benchmark_path_outside_the_folder_is_not_served(tmp_path):
    bench_dir = tmp_path / "benchmarks"
    bench_dir.mkdir()
    private = tmp_path / "benchmarks_private"
    private.mkdir()
    (private / "secret.json").write_text('{"summary": {}}', encoding="utf-8")

    assert benchmarks.load_benchmark(bench_dir, "../benchmarks_private/secret.json") is None
    assert benchmarks.load_benchmark(bench_dir, "..\benchmarks_private\\secret.json") is None


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
