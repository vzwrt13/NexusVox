"""`nexusvox.dashboard.analytics` stays the one import path after its queries moved to `analytics_*`."""

from nexusvox.dashboard import analytics

# The public names, and the one private name a test imports. A name may be added; ruff cannot see a dropped one,
# because the re-exports are marked `noqa: F401`.
EXPORTED = """
    APP_NAMES MAX_APP_ROWS OTHER_APP UNKNOWN_APP _command_kind app_label get_accuracy_over_time get_activity_heatmap
    get_app_breakdown get_dictionary_usage get_flagged_transcriptions
    get_language_distribution get_latency_over_time get_learn_outcomes get_model_breakdown get_model_speed
    get_most_corrected
    get_overview get_peak_usage_hours get_top_words get_transcription_speed get_transcriptions_over_time
    get_unreviewed_transcriptions
    get_upload_overview get_week get_words_per_day
""".split()


def test_every_name_is_still_exported():
    assert [name for name in EXPORTED if not hasattr(analytics, name)] == []
