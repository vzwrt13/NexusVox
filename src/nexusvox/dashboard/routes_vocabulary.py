"""Dashboard routes: the correction dictionary and polish presets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify

from .routes_base import _json_object, _typed

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """Dictionary entries, their suggestions and the polish presets."""

    @app.route("/api/dictionary")
    def get_dictionary():
        return jsonify(api.get_dictionary())

    @app.route("/api/dictionary", methods=["POST"])
    def add_dictionary_entry():
        data = _json_object()
        return jsonify(api.add_dictionary_entry(data.get("wrong", ""), data.get("right", "")))

    @app.route("/api/dictionary/<int:entry_id>", methods=["DELETE"])
    def delete_dictionary_entry(entry_id):
        return jsonify(api.delete_dictionary_entry(entry_id))

    @app.route("/api/polish-presets")
    def get_polish_presets():
        return jsonify(api.get_polish_presets())

    @app.route("/api/polish-presets", methods=["POST"])
    def save_polish_preset():
        data = _json_object()
        resp = api.save_polish_preset(
            data.get("name", ""),
            data.get("instruction", ""),
            _typed(data, "is_default", bool, False, "true or false"),
            data.get("id"),
        )
        return jsonify(resp), (200 if resp.get("ok") else 400)

    @app.route("/api/polish-presets/<int:preset_id>", methods=["DELETE"])
    def delete_polish_preset(preset_id):
        return jsonify(api.delete_polish_preset(preset_id))

    @app.route("/api/dictionary/suggestions")
    def get_dictionary_suggestions():
        return jsonify(api.get_dictionary_suggestions())

    @app.route("/api/dictionary/suggestions/<int:suggestion_id>/accept", methods=["POST"])
    def accept_dictionary_suggestion(suggestion_id):
        return jsonify(api.accept_dictionary_suggestion(suggestion_id))

    @app.route("/api/dictionary/suggestions/<int:suggestion_id>", methods=["DELETE"])
    def delete_dictionary_suggestion(suggestion_id):
        return jsonify(api.delete_dictionary_suggestion(suggestion_id))
