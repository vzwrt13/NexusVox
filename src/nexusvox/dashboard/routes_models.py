"""Dashboard routes: models and the compute device."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """Listing and switching the transcription model, and the compute device."""

    @app.route("/api/models")
    def get_models():
        return jsonify(api.get_available_models())

    @app.route("/api/models/current")
    def get_current_model():
        return jsonify(api.get_current_model())

    @app.route("/api/models/switch", methods=["POST"])
    def switch_model():
        data = request.get_json(force=True)
        return jsonify(api.set_model(data.get("model", "")))

    @app.route("/api/models/status")
    def model_status():
        return jsonify(api.get_model_status())

    # ---- Device endpoints --------------------------------------------------

    @app.route("/api/device")
    def get_device():
        return jsonify(api.get_device())

    @app.route("/api/device", methods=["POST"])
    def set_device():
        data = request.get_json(force=True)
        return jsonify(api.set_device(data.get("device", "auto")))
