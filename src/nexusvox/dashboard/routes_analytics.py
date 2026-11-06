"""Dashboard routes: usage statistics and the Dev tab's benchmarks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request

if TYPE_CHECKING:
    from ..config import ExperimentalConfig
    from .api import DashboardAPI


def register(app: Flask, api: DashboardAPI, experimental: ExperimentalConfig) -> None:
    """The Analytics tab's queries; the benchmarks only with `[experimental].dev_tab`."""

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

    @app.route("/api/latency-trend")
    def latency_trend():
        period = request.args.get("period", "day")
        start, end = _date_args()
        return jsonify(api.get_latency_trend(period, start, end))

    @app.route("/api/model-breakdown")
    def model_breakdown():
        start, end = _date_args()
        return jsonify(api.get_model_breakdown(start, end))

    @app.route("/api/accuracy")
    def accuracy():
        period = request.args.get("period", "day")
        start, end = _date_args()
        return jsonify(api.get_accuracy(period, start, end))

    @app.route("/api/app-breakdown")
    def app_breakdown():
        start, end = _date_args()
        return jsonify(api.get_app_breakdown(start, end))

    # ---- Benchmark endpoints (Dev tab, experimental) -----------------------

    if experimental.dev_tab:

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
