"""Dashboard routes: the Settings tab, its language model and the translator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request

from .routes_base import _json_object, _switch

if TYPE_CHECKING:
    from .api import DashboardAPI


_FEEDBACK_STATUS = {
    "invalid": 400,
    "too_large": 413,
    "disabled": 404,
    "stale": 409,
    "rate": 429,
    "daily_cap": 429,
    "offline": 503,
}


def _feedback_status(resp: dict) -> int:
    """200 for success; a refusal keeps a status the page can tell apart (an unknown cause is a bad gateway)."""
    return 200 if "error" not in resp else _FEEDBACK_STATUS.get(resp["error"], 502)


def register(app: Flask, api: DashboardAPI) -> None:
    """The settings page's switches, keys and language model."""

    @app.route("/api/settings")
    def get_settings():
        return jsonify(api.get_settings())

    @app.route("/api/settings/hotkey-mode", methods=["POST"])
    def set_hotkey_mode():
        data = _json_object()
        result = api.set_hotkey_mode(data.get("mode", ""))
        return jsonify(result), (400 if "error" in result else 200)

    @app.route("/api/settings/hotkey", methods=["POST"])
    def set_hotkey():
        data = _json_object()
        modifiers = data.get("modifiers", [])
        if not isinstance(modifiers, list):
            return jsonify({"error": "modifiers must be a list", **api.get_settings()}), 400
        result = api.set_hotkey(modifiers, data.get("key", ""))
        return jsonify(result), (400 if "error" in result else 200)

    @app.route("/api/settings/learn-from-edits", methods=["POST"])
    def set_learn_from_edits():
        data = _json_object()
        return jsonify(api.set_learn_from_edits(_switch(data, False)))

    @app.route("/api/settings/learn-from-edits-status")
    def get_learn_status():
        return jsonify(api.get_learn_status())

    @app.route("/api/settings/paste-key", methods=["POST"])
    def set_paste_key():
        data = _json_object()
        resp = api.set_paste_key(data.get("key", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/polish-key", methods=["POST"])
    def set_polish_key():
        data = _json_object()
        resp = api.set_polish_key(data.get("key", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/feedback-mode", methods=["POST"])
    def set_feedback_mode():
        data = _json_object()
        resp = api.set_feedback_mode(data.get("mode", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/cleanup-level", methods=["POST"])
    def set_cleanup_level():
        data = _json_object()
        resp = api.set_cleanup_level(data.get("level", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/polish-terminals", methods=["POST"])
    def set_polish_in_terminals():
        data = _json_object()
        return jsonify(api.set_polish_in_terminals(_switch(data, False)))

    @app.route("/api/settings/llm-model", methods=["POST"])
    def set_llm_model():
        data = _json_object()
        resp = api.set_llm_model(data.get("model", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/llm-models")
    def get_llm_models():
        return jsonify(api.get_llm_models())

    @app.route("/api/settings/llm-servers", methods=["POST", "DELETE"])
    def llm_servers():
        data = _json_object()
        if request.method == "DELETE":
            resp = api.remove_llm_server(data.get("url", ""))
        else:
            resp = api.add_llm_server(data.get("url", ""), data.get("api_key", ""), data.get("name", ""))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/ollama-start", methods=["POST"])
    def start_ollama():
        _json_object()
        resp = api.start_ollama()
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/llm-select", methods=["POST"])
    def select_llm_server_model():
        data = _json_object()
        resp = api.select_llm_server_model(data.get("url", ""), data.get("model", ""), data.get("role", "main"))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/llm-per-task", methods=["POST"])
    def set_llm_per_task():
        return jsonify(api.set_llm_per_task(_switch(_json_object(), None)))

    @app.route("/api/settings/llm-task-role", methods=["POST"])
    def set_llm_task_role():
        data = _json_object()
        resp = api.set_llm_task_role(data.get("task"), data.get("role"))
        return jsonify(resp), (400 if "error" in resp else 200)

    @app.route("/api/settings/llm-pull", methods=["GET", "POST", "DELETE"])
    def llm_pull():
        if request.method == "POST":
            resp = api.start_llm_pull(_json_object().get("model", ""))
            return jsonify(resp), (400 if "error" in resp and resp.get("status") != "pulling" else 200)
        if request.method == "DELETE":
            return jsonify(api.cancel_llm_pull())
        return jsonify(api.get_llm_pull())

    @app.route("/api/settings/llm-status")
    def get_llm_status():
        return jsonify(api.get_llm_status())

    @app.route("/api/settings/mic-guard", methods=["POST"])
    def set_mic_guard():
        data = _json_object()
        return jsonify(api.set_mic_guard_enabled(_switch(data, False)))

    @app.route("/api/settings/log-full-text", methods=["POST"])
    def set_log_full_text():
        data = _json_object()
        return jsonify(api.set_log_full_text(_switch(data, None)))

    @app.route("/api/feedback/preview", methods=["POST"])
    def preview_feedback():
        data = _json_object()
        resp = api.preview_feedback(data.get("kind"), data.get("message"), data.get("include_log") is True)
        return jsonify(resp), _feedback_status(resp)

    @app.route("/api/feedback/send", methods=["POST"])
    def send_feedback():
        data = _json_object()
        resp = api.send_feedback(
            data.get("kind"), data.get("message"), data.get("include_log") is True, data.get("token")
        )
        return jsonify(resp), _feedback_status(resp)

    @app.route("/api/log-folder/open", methods=["POST"])
    def open_log_folder():
        resp = api.open_log_folder()
        return jsonify(resp), (500 if "error" in resp else 200)

    @app.route("/api/settings/store-audio", methods=["POST"])
    def set_store_audio():
        data = _json_object()
        return jsonify(api.set_store_audio(_switch(data, True)))

    @app.route("/api/settings/translate", methods=["POST"])
    def set_translate():
        data = _json_object()
        return jsonify(api.set_translate_enabled(_switch(data, False)))

    @app.route("/api/settings/translator-status")
    def get_translator_status():
        return jsonify(api.get_translator_status())
