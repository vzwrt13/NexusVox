"""SQLAlchemy query functions for dashboard analytics.

The queries live in `analytics_*`, one module per topic; every name stays importable from here.
"""

from __future__ import annotations

from .analytics_accuracy import (  # noqa: F401
    _word_edits,
    get_accuracy_over_time,
    get_dictionary_usage,
    get_learn_outcomes,
    get_most_corrected,
)
from .analytics_common import _apply_date_filter  # noqa: F401
from .analytics_overview import (  # noqa: F401
    _EMPTY_CLEANUP,
    _POLISH_TAGS,
    _TYPING_WPM,
    _command_kind,
    _command_row_stats,
    _is_polish_label,
    _speaker_count,
    _split_label,
    get_overview,
    get_upload_overview,
)
from .analytics_review import get_flagged_transcriptions, get_unreviewed_transcriptions  # noqa: F401
from .analytics_usage import (  # noqa: F401
    APP_NAMES,
    MAX_APP_ROWS,
    OTHER_APP,
    UNKNOWN_APP,
    app_label,
    get_activity_heatmap,
    get_app_breakdown,
    get_language_distribution,
    get_latency_over_time,
    get_model_breakdown,
    get_peak_usage_hours,
    get_top_words,
    get_transcriptions_over_time,
)
