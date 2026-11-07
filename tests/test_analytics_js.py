"""The Analytics tab's views (Overview, Your week, all charts): the markup, the wiring and the pure helpers."""

import json
import re
import shutil
import subprocess

import pytest
from dashboard_js import STATIC, dashboard_source

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def test_analytics_has_the_elements_its_script_fills():
    html = _html()
    source = (STATIC / "dashboard-analytics.js").read_text(encoding="utf-8")
    # Every element the new views look up by id exists (a stale id is null in the browser and breaks the view).
    ids = set(re.findall(r'getElementById\("([a-z0-9-]+)"\)', source))
    assert [i for i in sorted(ids) if f'id="{i}"' not in html] == []
    for view in ("overview", "week", "charts"):
        assert f'id="analytics-{view}"' in html


def test_the_overview_is_the_view_that_opens_and_the_other_two_are_hidden():
    html = _html()
    assert re.search(r'<div id="analytics-overview" class="analytics-view">', html)
    assert re.search(r'<div id="analytics-week" class="analytics-view" hidden>', html)
    assert re.search(r'<div id="analytics-charts" class="analytics-view" hidden>', html)


def test_the_overview_offers_the_four_ranges_with_30_days_active():
    html = _html()
    ranges = re.findall(r'<button type="button" class="period-btn( active)?" data-range="(\w+)"', html)
    assert ranges == [("", "7"), ("", "14"), (" active", "30"), ("", "all")]
    assert 'let overviewRange = "30"' in dashboard_source()


def test_the_overview_has_four_tiles_and_the_week_three():
    html = _html()
    overview = html[html.index('id="analytics-overview"') : html.index('id="analytics-week"')]
    week = html[html.index('id="analytics-week"') : html.index('id="analytics-charts"')]
    assert overview.count('<div class="stat-card') == 4
    assert week.count('<div class="stat-card') == 3


def test_every_chart_and_table_is_still_on_the_charts_view():
    html = _html()
    charts = html[html.index('id="analytics-charts"') :]
    for element_id in (
        "stat-total",
        "chart-timeline",
        "chart-language",
        "chart-hours",
        "chart-latency",
        "model-breakdown",
        "app-breakdown",
        "chart-accuracy",
        "dictionary-usage",
        "chart-words",
        "heatmap-container",
        "btn-apply-dates",
    ):
        assert f'id="{element_id}"' in charts, element_id


def test_opening_the_tab_opens_the_view_and_the_tab_button_keeps_it_in_the_url():
    source = dashboard_source()
    assert re.search(r'if \(name === "analytics"\) openAnalyticsTab\(\);', source)
    assert 'name === "analytics" ? analyticsViewHash()' in source


def test_home_links_this_week_to_the_week_view():
    html = _html()
    assert re.search(r'<a href="#analytics/week" class="home-link" data-home-go="analytics/week">Your week</a>', html)


def run_js(body: str, hash_: str = ""):
    analytics = (STATIC / "dashboard-analytics.js").read_text(encoding="utf-8")
    home = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")

    def fn(source: str, name: str) -> str:
        return re.search(rf"^function {name}\(.*?^}}\n", source, re.S | re.M).group(0)

    helpers = fn(home, "localIsoDate") + "".join(
        fn(analytics, name)
        for name in (
            "analyticsViewHash",
            "analyticsViewFromHash",
            "parseIsoDate",
            "addDaysIso",
            "mondayOfIso",
            "formatShortDate",
            "formatWeekRange",
            "pluralize",
            "weekHeadline",
            "formatRecordedTime",
        )
    )
    helpers = fn(home, "formatSavedMinutes") + helpers
    helpers = re.search(r"^const WEEK_SAME_AS_BEFORE_PCT = \d+;$", analytics, re.M).group(0) + helpers
    # The headline formats numbers like the browser; Node's own locale would make the expected text machine-dependent.
    helpers = (
        "Number.prototype.toLocaleString = function () { return new Intl.NumberFormat('en-US').format(this); };"
        + helpers
    )
    consts = 'const ANALYTICS_VIEWS = ["overview", "week", "charts"]; let analyticsView = "overview";'
    window = f"const window = {{ location: {{ hash: {json.dumps(hash_)} }} }};"
    out = subprocess.run(
        ["node", "-e", f"{window}\n{consts}\n{helpers}\n{body}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


@needs_node
def test_a_week_starts_on_monday():
    got = run_js(
        "console.log(JSON.stringify(['2026-11-02', '2026-11-05', '2026-11-08', '2026-11-09', '2026-01-01']"
        ".map(mondayOfIso)))"
    )
    assert got == ["2026-11-02", "2026-11-02", "2026-11-02", "2026-11-09", "2025-12-29"]


@needs_node
def test_days_are_added_across_month_and_year_ends():
    got = run_js(
        "console.log(JSON.stringify([addDaysIso('2026-11-02', -7), addDaysIso('2026-12-28', 7), "
        "addDaysIso('2026-03-01', -1)]))"
    )
    assert got == ["2026-10-26", "2027-01-04", "2026-02-28"]


@needs_node
def test_the_week_range_names_both_ends_and_the_year():
    got = run_js("console.log(JSON.stringify(formatWeekRange('2026-12-28', '2027-01-03')))")
    assert got.endswith("2027") and "28" in got and "3" in got


@needs_node
@pytest.mark.parametrize(
    ("hash_", "view"),
    [
        ("#analytics", "overview"),
        ("#analytics/week", "week"),
        ("#analytics/charts", "charts"),
        ("#analytics/nonsense", "overview"),
        ("#home", "overview"),  # not this tab's address: the view it was on stays
    ],
)
def test_the_view_comes_from_the_address(hash_, view):
    assert run_js("console.log(JSON.stringify(analyticsViewFromHash()))", hash_) == view


@needs_node
def test_the_address_names_the_view():
    assert run_js('analyticsView = "week"; console.log(JSON.stringify(analyticsViewHash()))') == "#analytics/week"
    assert run_js("console.log(JSON.stringify(analyticsViewHash()))") == "#analytics"


def _week(**changes):
    week = {
        "words": 1200,
        "days_dictated": 4,
        "time_saved_minutes": 22,
        "previous_words": 1000,
        "meetings": {"count": 0, "seconds": 0},
    }
    return {**week, **changes}


@needs_node
def test_headline_compares_with_the_week_before():
    def headline(**changes):
        return run_js(f"console.log(JSON.stringify(weekHeadline({json.dumps(_week(**changes))})))")

    assert headline() == (
        "You dictated 1,200 words on 4 days, saving about 22 min of typing. That is 20% more than the week before."
    )
    assert headline(words=800, days_dictated=1, time_saved_minutes=0.4) == (
        "You dictated 800 words on 1 day. That is 20% less than the week before."
    )
    assert headline(words=1020).endswith("That is about the same as the week before.")
    assert "week before" not in headline(previous_words=0)


@needs_node
def test_headline_without_dictation_mentions_meetings():
    def headline(count):
        week = _week(words=0, meetings={"count": count, "seconds": 600})
        return run_js(f"console.log(JSON.stringify(weekHeadline({json.dumps(week)})))")

    assert headline(2) == "No dictation, but 2 meetings recorded."
    assert headline(1) == "No dictation, but 1 meeting recorded."
    assert run_js(f"console.log(JSON.stringify(weekHeadline({json.dumps(_week(words=0))})))") == (
        "No dictation in this week."
    )


@needs_node
def test_a_meeting_under_a_minute_is_not_shown_as_zero_minutes():
    got = run_js("console.log(JSON.stringify([25, 59, 60, 90, 3600, 5400].map(formatRecordedTime)))")
    assert got == ["under 1 min", "under 1 min", "1 min", "2 min", "1 h 0 min", "1 h 30 min"]


def test_the_headline_is_built_in_the_page_not_sent_by_the_server():
    source = (STATIC / "dashboard-analytics.js").read_text(encoding="utf-8")
    assert "weekHeadline(week)" in source and "week.headline" not in source


def test_opening_the_week_view_starts_at_this_week():
    source = (STATIC / "dashboard-analytics.js").read_text(encoding="utf-8")
    block = re.search(r'if \(view === "week"\) \{(.*?)\n  \}', source, re.S).group(1)
    assert "weekMonday = null;" in block and block.index("weekMonday = null;") < block.index("loadWeek()")


def test_the_words_chart_names_the_year_when_the_range_spans_years():
    source = (STATIC / "dashboard-analytics.js").read_text(encoding="utf-8")
    assert "formatShortDate(iso, spansYears)" in source
    assert "d.labels[0].slice(0, 4) !== d.labels[d.labels.length - 1].slice(0, 4)" in source


def test_home_shows_the_week_that_its_link_opens():
    home = (STATIC / "dashboard-home.js").read_text(encoding="utf-8")
    assert 'fetch("/api/week")' in home and "/api/overview" not in home
