"""Dashboard routes: file upload transcription and stored transcripts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_file

if TYPE_CHECKING:
    from .api import DashboardAPI


def _attachment(filename: str) -> str:
    """A Content-Disposition value for a download; non-ASCII names go in the RFC 5987 form."""
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def register(app: Flask, api: DashboardAPI) -> None:
    """Uploading a file, following its job, and the stored transcripts with their audio and exports."""

    @app.route("/api/file-transcribe", methods=["POST"])
    def file_transcribe():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file provided"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "No filename"}), 400
        file_bytes = f.read()
        diarize = request.form.get("diarize", "").lower() in ("1", "true", "on", "yes")
        cleanup_level = request.form.get("cleanup", "").strip().lower()
        result = api.start_file_transcription(file_bytes, f.filename, diarize=diarize, cleanup_level=cleanup_level)
        if result.get("ok"):
            return jsonify(result), 202
        return jsonify(result), 409 if result.get("busy") else 400

    @app.route("/api/file-transcribe/current")
    def file_transcribe_current():
        return jsonify({"job": api.get_current_file_job()})

    @app.route("/api/file-transcribe/<job_id>")
    def file_transcribe_status(job_id):
        job = api.get_file_job(job_id)
        if job is None:
            return jsonify({"ok": False, "error": "Unknown job"}), 404
        return jsonify(job)

    @app.route("/api/file-transcriptions")
    def file_transcriptions():
        limit = request.args.get("limit", 50, type=int)
        kind = request.args.get("kind")
        return jsonify(api.get_file_transcriptions(limit, kind if kind in ("upload", "meeting") else None))

    @app.route("/api/file-transcriptions/<int:tid>", methods=["PATCH"])
    def rename_file_transcription(tid):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "title" not in body:
            return jsonify({"ok": False, "error": "Expected a JSON body with a title"}), 400
        result = api.rename_file_transcription(tid, body["title"])
        return jsonify(result), (200 if result["ok"] else result["status"])

    @app.route("/api/file-transcriptions/<int:tid>", methods=["DELETE"])
    def delete_file_transcription(tid):
        result = api.delete_file_transcription(tid)
        if result["ok"]:
            return jsonify(result)
        return jsonify(result), (404 if result["error"].startswith("Unknown") else 500)

    @app.route("/api/file-transcriptions/<int:tid>/audio")
    def file_transcription_audio(tid):
        path = api.get_file_audio_path(tid)
        if path is None:
            return jsonify({"error": "Audio not found"}), 404
        return send_file(path, mimetype="audio/wav")

    @app.route("/api/file-transcriptions/<int:tid>/export/<target_id>")
    def export_file_transcription(tid, target_id):
        result = api.export_file_transcription(tid, target_id)
        if not result["ok"]:
            return jsonify({"ok": False, "error": result["error"]}), result["status"]
        return Response(
            result["data"],
            mimetype=result["mimetype"],
            headers={"Content-Disposition": _attachment(result["filename"])},
        )
