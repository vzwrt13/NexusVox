"""Dashboard routes: the first-run setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify

from .routes_base import _json_object

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """The microphone step's device list, choice and level meter, and the end of the setup."""

    @app.route("/api/setup")
    def setup_state():
        return jsonify(api.get_setup_state())

    @app.route("/api/setup/microphones")
    def setup_microphones():
        return jsonify(api.get_microphones())

    @app.route("/api/setup/microphone", methods=["POST"])
    def setup_set_microphone():
        data = _json_object()
        result = api.set_microphone(data.get("device", ""))
        return jsonify(result), (400 if "error" in result else 200)

    @app.route("/api/setup/mic-level")
    def setup_mic_level():
        return jsonify(api.read_mic_level())

    @app.route("/api/setup/mic-level/stop", methods=["POST"])
    def setup_mic_level_stop():
        return jsonify(api.stop_mic_level())

    @app.route("/api/setup/complete", methods=["POST"])
    def setup_complete():
        return jsonify(api.complete_setup())
