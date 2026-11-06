"""Dashboard routes: voice commands and the experimental OS commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify

from .routes_base import _json_object, _switch

if TYPE_CHECKING:
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI) -> None:
    """Voice command settings; the OS commands only with `[experimental].os_commands`."""
    experimental = api.experimental

    @app.route("/api/voice-commands")
    def get_voice_commands():
        return jsonify(api.get_voice_commands())

    @app.route("/api/voice-commands/enabled", methods=["POST"])
    def set_voice_commands_enabled():
        data = _json_object()
        return jsonify(api.set_voice_commands_enabled(_switch(data, True)))

    @app.route("/api/voice-commands/numbers", methods=["POST"])
    def set_voice_commands_numbers():
        data = _json_object()
        return jsonify(api.set_voice_commands_numbers(_switch(data, False)))

    @app.route("/api/voice-commands/press-enter", methods=["POST"])
    def set_voice_commands_press_enter():
        data = _json_object()
        return jsonify(api.set_voice_commands_press_enter(_switch(data, False)))

    @app.route("/api/voice-commands/symbols", methods=["POST"])
    def set_voice_commands_symbols():
        data = _json_object()
        return jsonify(api.set_voice_commands_symbols(data.get("symbols", [])))

    @app.route("/api/voice-commands/bypass-symbols", methods=["POST"])
    def set_voice_commands_bypass_symbols():
        data = _json_object()
        return jsonify(api.set_voice_commands_bypass_symbols(_switch(data, False)))

    @app.route("/api/voice-commands/structural", methods=["POST"])
    def set_voice_commands_structural():
        data = _json_object()
        return jsonify(api.set_voice_commands_structural(data.get("structural", [])))

    @app.route("/api/voice-commands/bypass-structural", methods=["POST"])
    def set_voice_commands_bypass_structural():
        data = _json_object()
        return jsonify(api.set_voice_commands_bypass_structural(_switch(data, False)))

    # ---- OS Commands endpoints (experimental) ------------------------------

    if experimental.os_commands:

        @app.route("/api/os-commands")
        def get_os_commands():
            return jsonify(api.get_os_commands())

        @app.route("/api/os-commands/enabled", methods=["POST"])
        def set_os_commands_enabled():
            data = _json_object()
            return jsonify(api.set_os_commands_enabled(_switch(data, False)))

        @app.route("/api/os-commands/apps", methods=["POST"])
        def set_os_commands_apps():
            data = _json_object()
            return jsonify(api.set_os_commands_apps(data.get("apps", {})))
