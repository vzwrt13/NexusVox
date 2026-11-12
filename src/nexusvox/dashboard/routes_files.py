"""Dashboard routes: file upload transcription and stored transcripts."""

from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from flask import Flask, Request, Response, jsonify, request, send_file

from ..transcription.file_transcribe import ALLOWED_EXTENSIONS, TEMP_PREFIX, file_extension
from .routes_base import _json_object

if TYPE_CHECKING:
    from .api import DashboardAPI


def _attachment(filename: str) -> str:
    """A Content-Disposition value for a download; non-ASCII names go in the RFC 5987 form."""
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


class _UploadSpool(io.FileIO):
    """Where Werkzeug writes an uploaded file: its own temporary file, so the body is stored once and the job can
    read it from there (Werkzeug's default spool is a second temporary file that would have to be copied).

    The file is deleted when the stream is closed - the end of the request, or a failed one (too large, dropped
    connection) - unless `release()` handed it to a job first.
    """

    def __init__(self) -> None:
        fd, name = tempfile.mkstemp(prefix=TEMP_PREFIX + "upload-")
        self.path = Path(name)
        self.keep = False
        super().__init__(fd, "r+")

    def release(self) -> Path:
        """Close the file and hand it over: the caller deletes it."""
        self.keep = True
        self.close()
        return self.path

    def close(self) -> None:
        super().close()
        if not self.keep:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:  # held by a scanner: the start-up sweep takes it
                pass


class _DiscardStream(io.BytesIO):
    """The body of a file that is refused by its extension: read from the socket and dropped, never stored."""

    def write(self, data) -> int:
        return len(data)


class _UploadRequest(Request):
    """A request whose upload route stores a file part in an `_UploadSpool`."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.path != "/api/file-transcribe":
            return super()._get_file_stream(total_content_length, content_type, filename, content_length)
        if file_extension(filename or "") not in ALLOWED_EXTENSIONS:
            return _DiscardStream()
        return _UploadSpool()


def register(app: Flask, api: DashboardAPI) -> None:
    """Uploading a file, following its job, and the stored transcripts with their audio and exports."""
    app.request_class = _UploadRequest

    @app.route("/api/file-transcribe", methods=["POST"])
    def file_transcribe():
        try:
            files = request.files  # parses the body: the file part lands in an _UploadSpool
        except OSError as exc:  # the disk is full
            return jsonify({"ok": False, "error": f"Could not save the upload: {exc}"}), 500
        if "file" not in files:
            return jsonify({"ok": False, "error": "No file provided"}), 400
        f = files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "No filename"}), 400
        problem = api.check_upload_name(f.filename)  # a refused extension was never stored (_DiscardStream)
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
        if not isinstance(f.stream, _UploadSpool):
            return jsonify({"ok": False, "error": "The upload was not stored"}), 500
        source = f.stream.release()  # the job owns the file from here on
        diarize = request.form.get("diarize", "").lower() in ("1", "true", "on", "yes")
        cleanup_level = request.form.get("cleanup", "").strip().lower()
        result = api.start_file_transcription(source, f.filename, diarize=diarize, cleanup_level=cleanup_level)
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
        body = _json_object()
        if "title" not in body:
            return jsonify({"ok": False, "error": "Expected a JSON body with a title"}), 400
        result = api.rename_file_transcription(tid, body["title"])
        return jsonify(result), (200 if result["ok"] else result["status"])

    @app.route("/api/file-transcriptions/<int:tid>", methods=["DELETE"])
    def delete_file_transcription(tid):
        result = api.delete_file_transcription(tid)
        if result["ok"]:
            return jsonify(result)
        return jsonify(result), (404 if result["error"].startswith("Unknown") else 500)

    @app.route("/api/file-transcriptions/<int:tid>/turns")
    def file_transcription_turns(tid):
        turns = api.get_speaker_turns(tid)
        if turns is None:
            return jsonify({"error": "Unknown file transcription"}), 404
        return jsonify({"turns": turns})

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
