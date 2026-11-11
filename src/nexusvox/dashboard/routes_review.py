"""Dashboard routes: flagged recordings, their audio and the Review tab."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, send_file

from .routes_base import _json_object, _optional_text, _typed

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """The review queue and recording audio; the flagged list only with `[experimental].flag_command`."""
    experimental = api.experimental

    # ---- Flagged / Corrections endpoints -----------------------------------

    if experimental.flag_command:

        @app.route("/api/flagged")
        def flagged_transcriptions():
            return jsonify(api.get_flagged_transcriptions())

        @app.route("/api/flagged/<int:tid>/correct", methods=["POST"])
        def correct_transcription(tid):
            data = _json_object()
            return jsonify(api.update_correction(tid, _typed(data, "corrected_text", str, "", "text")))

    @app.route("/api/audio/<int:tid>")
    def serve_audio(tid):
        path = api.get_audio_file_path(tid)
        if path is None:
            return jsonify({"error": "Audio not found"}), 404
        return send_file(path, mimetype="audio/wav")

    # ---- Review endpoints ----------------------------------------------------

    @app.route("/api/review")
    def review_transcriptions():
        before_id = request.args.get("before_id", type=int)
        return jsonify(api.get_unreviewed_transcriptions(before_id=before_id))

    @app.route("/api/review/<int:tid>/retry", methods=["POST"])
    def retry_transcription(tid):
        return jsonify(api.retry_transcription(tid))

    @app.route("/api/review/<int:tid>", methods=["POST"])
    def submit_review(tid):
        data = _json_object()
        is_correct = _typed(data, "is_correct", bool, True, "true or false")
        return jsonify(api.submit_review(tid, is_correct, _optional_text(data, "corrected_text")))
