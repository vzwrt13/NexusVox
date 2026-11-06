"""SQLAlchemy query functions for dashboard analytics.

The queries live in `analytics_*`, one module per topic; the public names stay importable from here.
"""

from __future__ import annotations

from .analytics_accuracy import (  # noqa: F401
    get_accuracy_over_time,
    get_dictionary_usage,
    get_learn_outcomes,
    get_most_corrected,
)
from .analytics_overview import _command_kind, get_overview, get_upload_overview  # noqa: F401
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
