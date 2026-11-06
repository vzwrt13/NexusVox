"""Dashboard routes: the Settings tab, its language model and the translator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request

from .routes_base import _switch

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """The settings page's switches, keys and language model."""

    @app.route("/api/settings")
    def get_settings():
        return jsonify(api.get_settings())

    @app.route("/api/settings/hotkey-mode", methods=["POST"])
    def set_hotkey_mode():
        data = request.get_json(force=True)
        result = api.set_hotkey_mode(data.get("mode", ""))
        return jsonify(result), (400 if "error" in result else 200)

    @app.route("/api/settings/hotkey", methods=["POST"])
    def set_hotkey():
        data = request.get_json(force=True)
        modifiers = data.get("modifiers", [])
        if not isinstance(modifiers, list):
            return jsonify({"error": "modifiers must be a list", **api.get_settings()}), 400
        result = api.set_hotkey(modifiers, data.get("key", ""))
        return jsonify(result), (400 if "error" in result else 200)

    @app.route("/api/settings/learn-from-edits", methods=["POST"])
    def set_learn_from_edits():
        data = request.get_json(force=True)
        return jsonify(api.set_learn_from_edits(_switch(data, False)))

    @app.route("/api/settings/learn-from-edits-status")
    def get_learn_status():
        return jsonify(api.get_learn_status())

    @app.route("/api/settings/paste-key", methods=["POST"])
    def set_paste_key():
        data = request.get_json(force=True)
        resp = api.set_paste_key(data.get("key", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/polish-key", methods=["POST"])
    def set_polish_key():
        data = request.get_json(force=True)
        resp = api.set_polish_key(data.get("key", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/feedback-mode", methods=["POST"])
    def set_feedback_mode():
        data = request.get_json(force=True)
        resp = api.set_feedback_mode(data.get("mode", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/cleanup-level", methods=["POST"])
    def set_cleanup_level():
        data = request.get_json(force=True)
        resp = api.set_cleanup_level(data.get("level", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/polish-terminals", methods=["POST"])
    def set_polish_in_terminals():
        data = request.get_json(force=True)
        return jsonify(api.set_polish_in_terminals(_switch(data, False)))

    @app.route("/api/settings/llm-model", methods=["POST"])
    def set_llm_model():
        data = request.get_json(force=True)
        resp = api.set_llm_model(data.get("model", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/llm-models")
    def get_llm_models():
        return jsonify(api.get_llm_models())

    @app.route("/api/settings/llm-pull", methods=["GET", "POST", "DELETE"])
    def llm_pull():
        if request.method == "POST":
            resp = api.start_llm_pull(request.get_json(force=True).get("model", ""))
            return jsonify(resp), (400 if "error" in resp and resp.get("status") != "pulling" else 200)
        if request.method == "DELETE":
            return jsonify(api.cancel_llm_pull())
        return jsonify(api.get_llm_pull())

    @app.route("/api/settings/llm-status")
    def get_llm_status():
        return jsonify(api.get_llm_status())

    @app.route("/api/settings/mic-guard", methods=["POST"])
    def set_mic_guard():
        data = request.get_json(force=True)
        return jsonify(api.set_mic_guard_enabled(_switch(data, False)))

    @app.route("/api/settings/log-full-text", methods=["POST"])
    def set_log_full_text():
        enabled = request.get_json(force=True).get("enabled")
        if not isinstance(enabled, bool):  # bool("false") would be True
            return jsonify({"error": "enabled must be true or false"}), 400
        return jsonify(api.set_log_full_text(enabled))

    @app.route("/api/log-folder/open", methods=["POST"])
    def open_log_folder():
        resp = api.open_log_folder()
        return jsonify(resp), (500 if "error" in resp else 200)

    @app.route("/api/settings/store-audio", methods=["POST"])
    def set_store_audio():
        data = request.get_json(force=True)
        return jsonify(api.set_store_audio(_switch(data, True)))

    @app.route("/api/settings/translate", methods=["POST"])
    def set_translate():
        data = request.get_json(force=True)
        return jsonify(api.set_translate_enabled(_switch(data, False)))

    @app.route("/api/settings/translator-status")
    def get_translator_status():
        return jsonify(api.get_translator_status())
