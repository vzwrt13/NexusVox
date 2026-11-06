"""Dashboard routes: flagged recordings, their audio and the Review tab."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, send_file

if TYPE_CHECKING:
    from ..config import ExperimentalConfig
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI, experimental: ExperimentalConfig) -> None:
    """The review queue and recording audio; the flagged list only with `[experimental].flag_command`."""
    # ---- Flagged / Corrections endpoints -----------------------------------

    if experimental.flag_command:

        @app.route("/api/flagged")
        def flagged_transcriptions():
            return jsonify(api.get_flagged_transcriptions())

        @app.route("/api/flagged/<int:tid>/correct", methods=["POST"])
        def correct_transcription(tid):
            data = request.get_json(force=True)
            return jsonify(api.update_correction(tid, data.get("corrected_text", "")))

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
        data = request.get_json(force=True)
        return jsonify(api.submit_review(tid, data.get("is_correct", True), data.get("corrected_text")))
