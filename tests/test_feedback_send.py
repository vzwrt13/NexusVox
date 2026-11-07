"""The Feedback tab's report: the log without dictated text, the request body and what the user is told."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

import httpx
import pytest

from nexusvox import __version__
from nexusvox.config import Config, SupportConfig
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.system import feedback_send
from nexusvox.system.feedback_send import (
    LOG_LINES,
    LOG_MAX_CHARS,
    REMOVED_MARK,
    FeedbackError,
    app_feedback_url,
    build_report,
    render_preview,
    report_log,
    send_report,
)
from nexusvox.system.logsetup import LOG_FORMAT_MARK, Text, setup_file_logging

URL = "https://nexusvox.app/api/app-feedback"
SPOKEN = "my secret sentence that goes on and on"


@pytest.fixture
def file_log(tmp_path):
    full = {"on": False}
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    directory = tmp_path / "logs"
    setup_file_logging(lambda: full["on"], directory=directory)
    try:
        yield directory, full
    finally:
        root.setLevel(previous)


def _log(directory, *lines):
    logger = logging.getLogger("nexusvox.test")
    for line in lines:
        if isinstance(line, tuple):
            logger.info(*line)
        else:
            logger.info(line)
    for handler in logging.getLogger().handlers:
        handler.flush()
    return report_log(directory)


@pytest.mark.parametrize("full_text", [False, True])
def test_dictated_text_is_never_in_the_report_log(file_log, full_text):
    directory, full = file_log
    full["on"] = full_text
    log = _log(
        directory,
        ("Transcription done: %s", Text(SPOKEN)),
        ("Cleanup: %r -> %r", Text(SPOKEN), Text("Another spoken thing")),
        ("Delta: %s (logprob=%s)", Text("multi\nline words"), -0.2),
        "plain line stays",
    )
    assert "plain line stays" in log and "Transcription done:" in log
    for secret in ("secret", "my secret sentence", "Another", "spoken thing", "multi", "line words"):
        assert secret not in log
    assert log.count(REMOVED_MARK) == 4
    assert not {"\ue000", "\ue001"} & set(log)


def test_a_text_that_holds_the_fence_characters_cannot_break_out(file_log):
    directory, _ = file_log
    log = _log(directory, ("said: %s", Text("a\ue001LEAK\ue000b")), "after")
    assert "LEAK" not in log and "after" in log


def test_lines_before_the_first_start_are_not_sent_but_a_restart_keeps_the_session_before_it(file_log):
    directory, _ = file_log
    path = directory / "nexusvox.log"
    old = "2026-01-01 [INFO] nexusvox: Transcription done: 'old version shortened' (30 chars)\n"
    path.write_text(old + path.read_text(encoding="utf-8"), encoding="utf-8")
    log = _log(directory, "before the crash")
    assert "old version" not in log and "before the crash" in log
    setup_file_logging(lambda: False, directory=directory)  # the app was restarted
    log = _log(directory, "after the restart")
    assert "before the crash" in log and "after the restart" in log and "old version" not in log
    path.write_text(old, encoding="utf-8")  # a file without the start mark: nothing is known to be safe
    assert report_log(directory) == ""


def test_a_big_log_is_read_from_its_end_without_leaking_a_cut_fence(file_log, monkeypatch):
    directory, _ = file_log
    monkeypatch.setattr(feedback_send, "_READ_LIMIT", 600)
    _log(
        directory, ("said: %s", Text("first line\nSECRET second line\nthird")), *[f"filler {i:03d}" for i in range(60)]
    )
    log = _log(directory, "last line")
    assert "SECRET" not in log and "third" not in log and "last line" in log and "filler 059" in log
    assert not {"\ue000", "\ue001"} & set(log)


def test_a_big_log_without_a_mark_in_its_end_still_counts_when_the_mark_is_at_the_start(file_log, monkeypatch):
    directory, _ = file_log
    monkeypatch.setattr(feedback_send, "_READ_LIMIT", 300)
    log = _log(directory, *[f"filler {i:03d}" for i in range(80)])
    assert log.endswith("filler 079") and LOG_FORMAT_MARK not in log
    path = directory / "nexusvox.log"
    path.write_text(path.read_text(encoding="utf-8").replace(LOG_FORMAT_MARK, "other"), encoding="utf-8")
    assert report_log(directory) == ""


def test_the_log_is_read_again_only_when_the_file_changed(file_log, monkeypatch):
    directory, _ = file_log
    _log(directory, "one")
    opened = []
    real_open = feedback_send.Path.open

    def counting_open(self, *args, **kwargs):
        opened.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(feedback_send.Path, "open", counting_open)
    assert report_log(directory) == report_log(directory) and opened == []
    assert "two" in _log(directory, "two") and len(opened) == 1


def test_the_log_is_the_last_300_lines_within_the_limit(file_log):
    directory, _ = file_log
    log = _log(directory, *[f"line {i}" for i in range(500)])
    lines = log.splitlines()
    assert len(lines) == LOG_LINES and lines[-1].endswith("line 499") and lines[0].endswith("line 200")
    big = _log(directory, *[f"{i:04d} " + "x" * 400 for i in range(300)])
    assert len(big) <= LOG_MAX_CHARS and big.splitlines()[-1].endswith("x" * 400)
    assert " 0299 " in big.splitlines()[-1]


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\Jane Doe\AppData\Local\NexusVox",
        "c:/users/Jane Doe/AppData",
        r"\\server\Users\Jane Doe\x.log",
        "/c/Users/Jane Doe/x.log",
        r"C:\Users\Jane",
        "Opened 'C:\\Users\\Jane Doe'",
    ],
)
def test_the_windows_user_name_in_paths_is_removed(file_log, path):
    directory, _ = file_log
    log = _log(directory, f"Model cache: {path}")
    assert "Jane" not in log and "Doe" not in log and "<user>" in log


def test_no_log_file_means_no_log(tmp_path):
    assert report_log(tmp_path / "missing") == ""


def test_the_report_has_the_worker_fields_and_the_log_only_when_asked(file_log):
    directory, _ = file_log
    _log(directory, "something")
    plain = build_report("bug", "  It crashes  ", False, log_directory=directory)
    assert set(plain) == {"kind", "message", "app_version", "windows_version", "hardware"}
    assert plain["message"] == "It crashes" and plain["app_version"] == __version__
    with_log = build_report("wish", "A wish", True, log_directory=directory)
    assert "something" in with_log["log"] and with_log["kind"] == "wish"
    assert "log" not in build_report("other", "No log yet", True, log_directory=directory / "missing")


@pytest.mark.parametrize(
    ("kind", "message", "code"),
    [
        ("bug", "ab", "invalid"),
        ("bug", "   ", "invalid"),
        ("bug", None, "invalid"),
        ("crash", "hello", "invalid"),
        ("bug", "x" * 5001, "too_large"),
    ],
)
def test_a_report_the_worker_would_refuse_is_stopped_here(kind, message, code):
    with pytest.raises(FeedbackError) as caught:
        build_report(kind, message, False)
    assert caught.value.code == code


def test_the_preview_shows_every_field_that_is_sent(file_log):
    directory, _ = file_log
    report = build_report("bug", "It crashes", True, log_directory=directory)
    text = render_preview(URL, report)
    for value in report.values():
        assert value in text
    assert text.startswith(f"To: {URL}")
    assert "Log: not included" in render_preview(URL, build_report("bug", "It crashes", False))


def test_the_url_is_the_worker_path_on_the_form_host():
    assert app_feedback_url("https://nexusvox.app/feedback?x=1") == URL
    assert app_feedback_url(" https://WWW.nexusvox.app/feedback ") == "https://www.nexusvox.app/api/app-feedback"
    assert app_feedback_url("") == "" and app_feedback_url("file:///C:/x") == ""


@pytest.mark.parametrize(
    "form_url",
    [
        "http://nexusvox.app/feedback",  # not encrypted
        "https://forms.example/r/abc",  # a third party never offered this endpoint
        "https://nexusvox.app.evil.example/feedback",
        "https://nexusvox.app@evil.example/feedback",
        "https://user:pw@nexusvox.app/feedback",
        "https://nexusvox.app:8443/feedback",
        "https://nexusvox.app:notaport/feedback",
    ],
)
def test_a_report_never_goes_to_another_host_or_over_plain_http(form_url):
    assert app_feedback_url(form_url) == ""


class _Response:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


def test_send_posts_json_with_the_app_user_agent_and_no_origin(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Response(200, {"ok": True})

    monkeypatch.setattr(feedback_send.httpx, "post", post)
    send_report(URL, {"kind": "bug", "message": "héllo ✓"})
    assert seen["url"] == URL
    assert seen["headers"]["User-Agent"] == f"NexusVox/{__version__}" and "Origin" not in seen["headers"]
    assert seen["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert "héllo ✓".encode() in seen["content"]


@pytest.mark.parametrize(
    ("response", "code", "retry", "words"),
    [
        (_Response(429, {"error": "rate", "scope": "visitor"}, {"Retry-After": "60"}), "rate", 60, "60 seconds"),
        (_Response(429, {"error": "daily_cap"}, {"Retry-After": "900"}), "daily_cap", None, "tomorrow"),
        (_Response(413, {"error": "too_large"}), "too_large", None, "too large"),
        (_Response(400, {"error": "invalid"}), "invalid", None, "did not accept"),
        (_Response(502, {"error": "delivery"}), "server", None, "HTTP 502"),
        (_Response(503, None), "server", None, "HTTP 503"),
    ],
)
def test_a_refusal_is_explained(monkeypatch, response, code, retry, words):
    monkeypatch.setattr(feedback_send.httpx, "post", lambda *a, **k: response)
    with pytest.raises(FeedbackError) as caught:
        send_report(URL, {})
    assert caught.value.code == code and caught.value.retry_after == retry and words in caught.value.message


@pytest.mark.parametrize("error", [httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
def test_no_connection_is_its_own_message(monkeypatch, error):
    def post(*a, **k):
        raise error

    monkeypatch.setattr(feedback_send.httpx, "post", post)
    with pytest.raises(FeedbackError) as caught:
        send_report(URL, {})
    assert caught.value.code == "offline" and "No connection" in caught.value.message


@pytest.fixture
def client(db, session_factory):
    def make(form_url="https://nexusvox.app/feedback"):
        config = Config(support=SupportConfig(feedback_form_url=form_url))
        return _create_app(DashboardAPI(session_factory, config, db=db)).test_client()

    return make


def test_a_lone_surrogate_from_the_page_is_replaced_before_it_is_shown_or_sent(monkeypatch):
    report = build_report("bug", "half an emoji \ud83d here", False)
    assert "\ud83d" not in report["message"] and "half an emoji" in report["message"]
    seen = {}
    monkeypatch.setattr(feedback_send.httpx, "post", lambda url, **kw: seen.update(kw) or _Response(200, {}))
    send_report(URL, {"kind": "bug", "message": "x \ud83d y"})  # a report built elsewhere still encodes
    assert b"x " in seen["content"]


def test_a_malformed_address_is_reported_not_raised(monkeypatch):
    def post(*a, **k):
        raise httpx.InvalidURL("bad port")

    monkeypatch.setattr(feedback_send.httpx, "post", post)
    with pytest.raises(FeedbackError) as caught:
        send_report("https://nexusvox.app:x/api", {})
    assert caught.value.code == "disabled"


def test_the_routes_preview_without_sending_and_send_on_post(client, monkeypatch):
    sent = []
    monkeypatch.setattr("nexusvox.dashboard.api_settings.send_report", lambda url, report: sent.append((url, report)))
    body = {"kind": "bug", "message": "It crashes", "include_log": False}
    with client() as c:
        preview = c.post("/api/feedback/preview", json=body)
        assert preview.status_code == 200 and "It crashes" in preview.get_json()["preview"] and sent == []
        reply = c.post("/api/feedback/send", json=body)
    assert reply.status_code == 200 and reply.get_json()["ok"] is True
    assert sent[0][0] == URL and sent[0][1]["message"] == "It crashes" and "log" not in sent[0][1]


def test_a_failed_send_reports_the_cause_and_status(client, monkeypatch):
    def refuse(url, report):
        raise FeedbackError("offline", "No connection to the feedback server.")

    monkeypatch.setattr("nexusvox.dashboard.api_settings.send_report", refuse)
    with client() as c:
        reply = c.post("/api/feedback/send", json={"kind": "bug", "message": "It crashes"})
        short = c.post("/api/feedback/send", json={"kind": "bug", "message": "no"})
    assert reply.status_code == 503 and reply.get_json()["error"] == "offline"
    assert short.status_code == 400


def test_the_log_is_only_included_for_a_true_flag(client, monkeypatch):
    monkeypatch.setattr("nexusvox.dashboard.api_settings.send_report", lambda url, report: None)
    monkeypatch.setattr("nexusvox.system.feedback_send.report_log", lambda directory=None: "a log line")
    with client() as c:
        body = {"kind": "bug", "message": "It crashes"}
        assert c.post("/api/feedback/send", json={**body, "include_log": "yes"}).get_json()["log_included"] is False
        assert c.post("/api/feedback/send", json={**body, "include_log": True}).get_json()["log_included"] is True


@pytest.mark.parametrize("form_url", ["", "https://forms.example/r/abc", "http://nexusvox.app/feedback"])
def test_sending_is_off_without_a_trusted_form_url(client, monkeypatch, form_url):
    called = []
    monkeypatch.setattr("nexusvox.dashboard.api_settings.send_report", lambda *a: called.append(a))
    with client(form_url) as c:
        body = {"kind": "bug", "message": "It crashes"}
        sent = c.post("/api/feedback/send", json=body)
        previewed = c.post("/api/feedback/preview", json=body)
        settings = c.get("/api/settings").get_json()
    assert sent.status_code == previewed.status_code == 404 and called == []
    assert settings["feedback_app_send"] is False
    assert bool(settings["feedback_url"]) is bool(form_url)  # the web form link stays whenever a URL is set


def test_the_default_form_url_offers_sending(client):
    with client() as c:
        assert c.get("/api/settings").get_json()["feedback_app_send"] is True


def test_send_posts_exactly_the_previewed_report_even_when_the_log_grew(client, monkeypatch):
    sent = []
    logs = iter(["log as previewed", "log after more lines"])
    monkeypatch.setattr("nexusvox.dashboard.api_settings.send_report", lambda url, report: sent.append(report))
    monkeypatch.setattr("nexusvox.system.feedback_send.report_log", lambda directory=None: next(logs))
    body = {"kind": "bug", "message": "It crashes", "include_log": True}
    with client() as c:
        token = c.post("/api/feedback/preview", json=body).get_json()["token"]
        assert c.post("/api/feedback/send", json={**body, "token": token}).get_json()["ok"] is True
        assert [r["log"] for r in sent] == ["log as previewed"]
        again = c.post("/api/feedback/send", json={**body, "token": token})  # one preview, one send
    assert again.status_code == 409 and len(sent) == 1


@pytest.mark.parametrize("change", [{"message": "Something else"}, {"kind": "wish"}, {"include_log": False}])
def test_a_send_after_the_inputs_changed_sends_nothing(client, monkeypatch, change):
    sent = []
    monkeypatch.setattr("nexusvox.dashboard.api_settings.send_report", lambda url, report: sent.append(report))
    body = {"kind": "bug", "message": "It crashes", "include_log": True}
    with client() as c:
        token = c.post("/api/feedback/preview", json=body).get_json()["token"]
        reply = c.post("/api/feedback/send", json={**body, **change, "token": token})
        wrong = c.post("/api/feedback/send", json={**body, "token": "nope"})
    assert reply.status_code == wrong.status_code == 409 and reply.get_json()["error"] == "stale" and sent == []


def test_the_log_marks_a_start_and_each_rollover(file_log):
    directory, _ = file_log
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler))
    assert LOG_FORMAT_MARK in (directory / "nexusvox.log").read_text(encoding="utf-8")
    handler.doRollover()
    assert LOG_FORMAT_MARK in (directory / "nexusvox.log").read_text(encoding="utf-8")
