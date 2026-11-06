"""Dashboard routes: meeting recording and its settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request

from .routes_base import _json_object, _switch

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """The meeting settings, and starting, stopping and listing recordings."""

    @app.route("/api/settings/meeting-hotkey", methods=["POST"])
    def set_meeting_hotkey():
        data = _json_object()
        modifiers = data.get("modifiers", [])
        if not isinstance(modifiers, list):
            return jsonify({"error": "modifiers must be a list", **api.get_settings()}), 400
        result = api.set_meeting_hotkey(modifiers, data.get("key", ""))
        return jsonify(result), (400 if "error" in result else 200)

    @app.route("/api/settings/meeting-devices", methods=["POST"])
    def set_meeting_devices():
        data = _json_object()
        return jsonify(api.set_meeting_devices(data.get("mic", ""), data.get("output", "")))

    @app.route("/api/settings/meeting-consent-hint", methods=["POST"])
    def set_meeting_consent_hint():
        data = _json_object()
        return jsonify(api.set_meeting_consent_hint(_switch(data, True)))

    @app.route("/api/settings/meeting-own-voice-label", methods=["POST"])
    def set_meeting_own_voice_label():
        data = _json_object()
        resp = api.set_meeting_own_voice_label(data.get("label", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/meeting-leave-dictation-out", methods=["POST"])
    def set_meeting_leave_dictation_out():
        data = _json_object()
        return jsonify(api.set_meeting_leave_dictation_out(_switch(data, True)))

    @app.route("/api/settings/meeting-store-audio", methods=["POST"])
    def set_meeting_store_audio():
        data = _json_object()
        return jsonify(api.set_meeting_store_audio(_switch(data, True)))

    @app.route("/api/settings/meeting-live-transcript", methods=["POST"])
    def set_meeting_live_transcript():
        data = _json_object()
        return jsonify(api.set_meeting_live_transcript(_switch(data, False)))

    # ---- Meeting recording ---------------------------------------------------

    @app.route("/api/meetings/live")
    def meeting_live():
        try:
            after = int(request.args.get("after", 0))
        except ValueError:
            after = 0
        return jsonify(api.get_meeting_live(after))

    @app.route("/api/meetings/status")
    def meeting_status():
        return jsonify(api.get_meeting_status())

    @app.route("/api/meetings/devices")
    def meeting_devices():
        return jsonify(api.get_meeting_devices())

    @app.route("/api/meetings/start", methods=["POST"])
    def meeting_start():
        result = api.start_meeting()
        return jsonify(result), (200 if result["ok"] else 500)

    @app.route("/api/meetings/pending")
    def meeting_pending():
        return jsonify(api.get_pending_meetings())

    @app.route("/api/meetings/pending/<name>/transcribe", methods=["POST"])
    def meeting_pending_transcribe(name):
        result = api.transcribe_pending_meeting(name)
        if result["ok"]:
            return jsonify(result)
        return jsonify(result), (409 if result.get("busy") else 404)

    @app.route("/api/meetings/pending/<name>", methods=["DELETE"])
    def meeting_pending_delete(name):
        result = api.delete_pending_meeting(name)
        return jsonify(result), (200 if result["ok"] else 404 if result["error"] == "Unknown meeting" else 500)

    @app.route("/api/meetings/stop", methods=["POST"])
    def meeting_stop():
        result = api.stop_meeting()
        return jsonify(result), (200 if result["ok"] or result.get("busy") else 500)
