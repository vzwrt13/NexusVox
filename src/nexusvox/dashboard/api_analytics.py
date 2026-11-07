"""Dashboard API: usage statistics and the Dev tab's benchmarks."""

from __future__ import annotations

from . import analytics
from . import benchmarks as bench
from .api_base import DashboardState


class AnalyticsMixin(DashboardState):
    """The Analytics tab's queries and the Dev tab's benchmark files."""

    # ---- Analytics ---------------------------------------------------------

    def get_overview(self, start: str | None = None, end: str | None = None) -> dict:
        return {
            **analytics.get_overview(self._sf, start=start, end=end),
            **analytics.get_upload_overview(self._sf, start=start, end=end),
        }

    def get_transcriptions_over_time(
        self, period: str = "day", start: str | None = None, end: str | None = None
    ) -> dict:
        return analytics.get_transcriptions_over_time(self._sf, period, start=start, end=end)

    def get_language_distribution(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_language_distribution(self._sf, start=start, end=end)

    def get_top_words(self, n: int = 20, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_top_words(self._sf, n, start=start, end=end)

    def get_peak_usage_hours(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_peak_usage_hours(self._sf, start=start, end=end)

    def get_activity_heatmap(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_activity_heatmap(self._sf, start=start, end=end)

    def get_latency_trend(self, period: str = "day", start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_latency_over_time(self._sf, period, start=start, end=end)

    def get_model_breakdown(self, start: str | None = None, end: str | None = None) -> list[dict]:
        return analytics.get_model_breakdown(self._sf, start=start, end=end)

    def get_model_speed(self, start: str | None = None, end: str | None = None) -> list[dict]:
        return analytics.get_model_speed(self._sf, start=start, end=end)

    def get_app_breakdown(self, start: str | None = None, end: str | None = None) -> list[dict]:
        return analytics.get_app_breakdown(self._sf, start=start, end=end)

    def get_words_per_day(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_words_per_day(self._sf, start, end)

    def get_week(self, week: str | None = None) -> dict:
        return analytics.get_week(self._sf, week)

    def get_accuracy(self, period: str = "day", start: str | None = None, end: str | None = None) -> dict:
        return {
            **analytics.get_accuracy_over_time(self._sf, period, start=start, end=end),
            "most_corrected": analytics.get_most_corrected(self._sf, start=start, end=end)["items"],
            "learn": analytics.get_learn_outcomes(self._sf, start=start, end=end),
            "dictionary": analytics.get_dictionary_usage(self._sf, start=start, end=end),
        }

    # ---- Benchmarks (Dev tab) ----------------------------------------------

    def get_benchmarks(self) -> list[dict]:
        """List all benchmark files with summary stats."""
        return bench.list_benchmarks(self._benchmarks_dir)

    def get_benchmark(self, filename: str) -> dict | None:
        """Load full benchmark data for a single file."""
        return bench.load_benchmark(self._benchmarks_dir, filename)

    def get_benchmark_comparison(self) -> dict:
        """Cross-model comparison with pass/fail against WER targets."""
        return bench.compare_benchmarks(self._benchmarks_dir)
