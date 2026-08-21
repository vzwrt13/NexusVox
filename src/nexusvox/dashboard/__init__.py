"""Dashboard server using Flask, opened in the default browser."""

from __future__ import annotations

import asyncio
import logging
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, send_file

from ..config import Config
from ..db import Database
from .api import DashboardAPI

if TYPE_CHECKING:
    from ..app import NexusVoxApp

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_PORT = 47392  # Uncommon port to avoid collisions.
_server_started = False
_lock = threading.Lock()


def _create_app(api: DashboardAPI) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR), static_url_path="")

    # Suppress Flask request logging in production.
    wlog = logging.getLogger("werkzeug")
    wlog.setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    # ---- Model endpoints ---------------------------------------------------

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

    # ---- Settings endpoints ------------------------------------------------

    @app.route("/api/settings")
    def get_settings():
        return jsonify(api.get_settings())

    @app.route("/api/settings/auto-language-detection", methods=["POST"])
    def set_auto_lang():
        data = request.get_json(force=True)
        return jsonify(api.set_auto_language_detection(data.get("enabled", False)))

    # ---- Voice Commands endpoints ------------------------------------------

    @app.route("/api/voice-commands")
    def get_voice_commands():
        return jsonify(api.get_voice_commands())

    @app.route("/api/voice-commands/enabled", methods=["POST"])
    def set_voice_commands_enabled():
        data = request.get_json(force=True)
        return jsonify(api.set_voice_commands_enabled(data.get("enabled", True)))

    @app.route("/api/voice-commands/numbers", methods=["POST"])
    def set_voice_commands_numbers():
        data = request.get_json(force=True)
        return jsonify(api.set_voice_commands_numbers(data.get("enabled", False)))

    @app.route("/api/voice-commands/symbols", methods=["POST"])
    def set_voice_commands_symbols():
        data = request.get_json(force=True)
        return jsonify(api.set_voice_commands_symbols(data.get("symbols", [])))

    @app.route("/api/voice-commands/bypass-symbols", methods=["POST"])
    def set_voice_commands_bypass_symbols():
        data = request.get_json(force=True)
        return jsonify(api.set_voice_commands_bypass_symbols(data.get("enabled", False)))

    # ---- OS Commands endpoints ---------------------------------------------

    @app.route("/api/os-commands")
    def get_os_commands():
        return jsonify(api.get_os_commands())

    @app.route("/api/os-commands/enabled", methods=["POST"])
    def set_os_commands_enabled():
        data = request.get_json(force=True)
        return jsonify(api.set_os_commands_enabled(data.get("enabled", False)))

    @app.route("/api/os-commands/apps", methods=["POST"])
    def set_os_commands_apps():
        data = request.get_json(force=True)
        return jsonify(api.set_os_commands_apps(data.get("apps", {})))

    # ---- Analytics endpoints -----------------------------------------------

    def _date_args():
        """Extract optional start/end date query params."""
        return request.args.get("start") or None, request.args.get("end") or None

    @app.route("/api/overview")
    def overview():
        start, end = _date_args()
        return jsonify(api.get_overview(start, end))

    @app.route("/api/transcriptions-over-time")
    def transcriptions_over_time():
        period = request.args.get("period", "day")
        start, end = _date_args()
        return jsonify(api.get_transcriptions_over_time(period, start, end))

    @app.route("/api/language-distribution")
    def language_distribution():
        start, end = _date_args()
        return jsonify(api.get_language_distribution(start, end))

    @app.route("/api/top-words")
    def top_words():
        n = request.args.get("n", 20, type=int)
        start, end = _date_args()
        return jsonify(api.get_top_words(n, start, end))

    @app.route("/api/peak-usage-hours")
    def peak_usage_hours():
        start, end = _date_args()
        return jsonify(api.get_peak_usage_hours(start, end))

    @app.route("/api/activity-heatmap")
    def activity_heatmap():
        start, end = _date_args()
        return jsonify(api.get_activity_heatmap(start, end))

    # ---- Flagged / Corrections endpoints -----------------------------------

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
        return jsonify(api.get_unreviewed_transcriptions())

    @app.route("/api/review/<int:tid>", methods=["POST"])
    def submit_review(tid):
        data = request.get_json(force=True)
        return jsonify(api.submit_review(tid, data.get("is_correct", True), data.get("corrected_text")))

    @app.route("/api/confidence-trend")
    def confidence_trend():
        period = request.args.get("period", "day")
        start, end = _date_args()
        return jsonify(api.get_confidence_trend(period, start, end))

    # ---- File Upload Transcription endpoints --------------------------------

    @app.route("/api/file-transcribe", methods=["POST"])
    def file_transcribe():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "No file provided"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "No filename"}), 400
        file_bytes = f.read()
        result = api.upload_and_transcribe(file_bytes, f.filename)
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.route("/api/file-transcriptions")
    def file_transcriptions():
        limit = request.args.get("limit", 50, type=int)
        return jsonify(api.get_file_transcriptions(limit))

    # ---- Benchmark endpoints (Dev tab) -------------------------------------

    @app.route("/api/benchmarks")
    def benchmarks_list():
        return jsonify(api.get_benchmarks())

    @app.route("/api/benchmarks/compare")
    def benchmarks_compare():
        return jsonify(api.get_benchmark_comparison())

    @app.route("/api/benchmarks/<filename>")
    def benchmark_detail(filename):
        data = api.get_benchmark(filename)
        if data is None:
            return jsonify({"error": "Benchmark not found"}), 404
        return jsonify(data)

    return app


def open_dashboard(db: Database, config: Config, app: NexusVoxApp | None = None) -> None:
    """Start the Flask server (once) and open the dashboard in the browser."""
    global _server_started

    with _lock:
        if not _server_started:
            # Build the thread→async bridge for model switching
            on_model_switch = None
            get_switch_status = None
            if app is not None:

                def on_model_switch(model_id: str) -> None:
                    if app._loop is not None:
                        asyncio.run_coroutine_threadsafe(app.switch_model(model_id), app._loop)

                get_switch_status = app.get_switch_status

            # Resolve benchmarks dir relative to the project root (CWD).
            benchmarks_dir = Path.cwd() / "benchmarks"

            dashboard_api = DashboardAPI(
                db._session_factory,
                config,
                db=db,
                on_model_switch=on_model_switch,
                get_switch_status=get_switch_status,
                benchmarks_dir=benchmarks_dir,
            )
            flask_app = _create_app(dashboard_api)

            thread = threading.Thread(
                target=flask_app.run,
                kwargs={"host": "127.0.0.1", "port": _PORT, "use_reloader": False},
                daemon=True,
                name="dashboard-server",
            )
            thread.start()
            _server_started = True
            logger.info("Dashboard server started on http://127.0.0.1:%d", _PORT)

    webbrowser.open(f"http://127.0.0.1:{_PORT}")
