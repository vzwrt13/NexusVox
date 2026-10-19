"""Tests for the extension interface: entry-point loading, the three hooks and their dashboard wiring."""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Blueprint, jsonify

import nexusvox.extensions as ext
from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.extensions import (
    API_VERSION,
    DashboardPanel,
    ExportTarget,
    Extension,
    ExtensionRegistry,
    FileTranscript,
    UploadAction,
)
from nexusvox.transcriber import TranscriptionResult, Word


class V1Extension(Extension):
    """What a well-formed extension looks like: it states the API version it was written for."""

    api_version = API_VERSION


class DummyExtension(V1Extension):
    """Records what it is handed and offers one panel and one export target."""

    name = "dummy"
    version = "1.2.3"

    def __init__(self, static_dir):
        self.seen: list[FileTranscript] = []
        blueprint = Blueprint("dummy_panel", __name__)

        @blueprint.route("/hello")
        def hello():
            return jsonify({"hello": "world"})

        self._panel = DashboardPanel("dummy-tab", "Dummy", static_dir, blueprint=blueprint)

    def on_file_transcribed(self, transcript):
        self.seen.append(transcript)
        return {"words": len(transcript.text.split()), "note": "Dummy saw it."}

    def dashboard_panels(self):
        return [self._panel]

    def export_targets(self):
        return [ExportTarget("dummy-csv", "Dummy CSV", ".csv", "text/csv", lambda t: f"id,text\n{t.id},{t.text}\n")]


@pytest.fixture
def dummy(tmp_path):
    static = tmp_path / "panel-static"
    static.mkdir()
    (static / "panel.js").write_text("NexusVoxExt.registerPanel('dummy-tab', () => {});", encoding="utf-8")
    return DummyExtension(static)


@pytest.fixture
def registry(dummy):
    reg = ExtensionRegistry()
    assert reg.add(dummy)
    return reg


@pytest.fixture
def ext_client(db, session_factory, registry, monkeypatch):
    api = DashboardAPI(session_factory, Config(), db=db, extensions=registry)
    app = _create_app(api)
    app.config["TESTING"] = True
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)
    with app.test_client() as client:
        yield client


def fake_entry_point(name, loaded=None, error=None):
    def load():
        if error:
            raise error
        return loaded

    return SimpleNamespace(name=name, load=load)


def stub_transcription(monkeypatch, text="hello from the file"):
    result = TranscriptionResult()
    result.finalize(text=text)
    result.words = [Word("hello", 0.0, 0.4), Word("from", 0.4, 0.6, 1)]

    async def fake_transcribe(wav, cfg, **kwargs):
        return result

    monkeypatch.setattr("nexusvox.dashboard.api.transcribe_file", fake_transcribe)


def upload(client, wav_bytes, name="meeting.wav"):
    resp = client.post(
        "/api/file-transcribe",
        data={"file": (io.BytesIO(wav_bytes), name)},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202, resp.get_json()
    job_id = resp.get_json()["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/file-transcribe/{job_id}").get_json()
        if job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("file job did not end")


# ---- Loading --------------------------------------------------------------


def test_the_entry_point_group_is_nexusvox_extensions():
    assert ext.ENTRY_POINT_GROUP == "nexusvox.extensions"
    assert isinstance(API_VERSION, int)


def test_load_accepts_a_class_an_instance_and_a_factory(tmp_path):
    class A(V1Extension):
        name = "a"

    class B(V1Extension):
        name = "b"

    class C(V1Extension):
        name = "c"

    reg = ExtensionRegistry()
    reg.load([fake_entry_point("a", A), fake_entry_point("b", B()), fake_entry_point("c", lambda: C())])

    assert [e.name for e in reg.extensions] == ["a", "b", "c"]


def test_load_asks_the_installed_entry_points_of_the_group(monkeypatch):
    class A(V1Extension):
        name = "a"

    asked = []

    def fake_entry_points(**kwargs):
        asked.append(kwargs)
        return [fake_entry_point("a", A)]

    monkeypatch.setattr(ext, "entry_points", fake_entry_points)
    reg = ExtensionRegistry()
    reg.load()

    assert asked == [{"group": "nexusvox.extensions"}]
    assert [e.name for e in reg.extensions] == ["a"]


def test_a_failing_extension_is_logged_and_skipped(caplog):
    class Good(V1Extension):
        name = "good"

    class Exploding(V1Extension):
        name = "exploding"

        def __init__(self):
            raise RuntimeError("boom")

    reg = ExtensionRegistry()
    reg.load(
        [
            fake_entry_point("broken-import", error=ImportError("no module")),
            fake_entry_point("exploding", Exploding),
            fake_entry_point("wrong-type", object),
            fake_entry_point("good", Good),
        ]
    )

    assert [e.name for e in reg.extensions] == ["good"]
    assert "broken-import" in caplog.text and "exploding" in caplog.text and "wrong-type" in caplog.text


def test_a_failed_entry_point_lookup_loads_nothing(monkeypatch, caplog):
    def broken(**kwargs):
        raise OSError("metadata unreadable")

    monkeypatch.setattr(ext, "entry_points", broken)
    reg = ExtensionRegistry()
    reg.load()

    assert reg.extensions == []
    assert "nexusvox.extensions" in caplog.text


def test_an_extension_for_another_api_version_is_skipped(caplog):
    class Future(V1Extension):
        name = "future"
        api_version = API_VERSION + 1

    reg = ExtensionRegistry()

    assert reg.add(Future) is False
    assert reg.extensions == []
    assert "future" in caplog.text


def test_an_extension_without_a_name_or_with_a_taken_name_is_skipped():
    class Nameless(V1Extension):
        pass

    class First(V1Extension):
        name = "same"

    class Second(V1Extension):
        name = "same"

    reg = ExtensionRegistry()

    assert reg.add(Nameless) is False
    assert reg.add(First) is True
    assert reg.add(Second) is False
    assert len(reg.extensions) == 1


def test_a_failing_registration_hook_skips_the_extension():
    class Bad(V1Extension):
        name = "bad"

        def dashboard_panels(self):
            raise RuntimeError("no panels for you")

    reg = ExtensionRegistry()

    assert reg.add(Bad) is False
    assert reg.panels() == [] and reg.extensions == []


def test_malformed_and_duplicate_panel_and_target_ids_are_dropped(tmp_path):
    class Messy(V1Extension):
        name = "messy"

        def dashboard_panels(self):
            return [
                DashboardPanel("ok", "Fine", tmp_path),
                DashboardPanel("Has Spaces", "Bad id", tmp_path),
                DashboardPanel("ok", "Twice", tmp_path),
                "not a panel",
            ]

        def export_targets(self):
            return [ExportTarget("../etc", "Bad", ".x", "text/plain", lambda t: "")]

    reg = ExtensionRegistry()
    reg.add(Messy)

    assert [p.title for p in reg.panels()] == ["Fine"]
    assert reg.export_targets() == []


def test_a_panel_id_taken_by_an_earlier_extension_is_dropped(tmp_path):
    class One(V1Extension):
        name = "one"

        def dashboard_panels(self):
            return [DashboardPanel("shared", "One", tmp_path)]

    class Two(V1Extension):
        name = "two"

        def dashboard_panels(self):
            return [DashboardPanel("shared", "Two", tmp_path)]

    reg = ExtensionRegistry()
    reg.add(One)
    reg.add(Two)

    assert [p.title for p in reg.panels()] == ["One"]


def test_load_extensions_fills_the_shared_registry_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ext, "_default", ExtensionRegistry())
    monkeypatch.setattr(ext, "_default_loaded", False)
    monkeypatch.setattr(ExtensionRegistry, "load", lambda self, points=None: calls.append(1))

    assert ext.load_extensions() is ext.load_extensions()
    assert calls == [1]


# ---- Hook: file transcribed -------------------------------------------------


def test_the_hook_results_and_notes_are_collected_per_extension(registry, dummy):
    transcript = FileTranscript(id=1, text="one two three", language="en", duration_ms=10, original_filename="a.wav")

    results, notes = registry.file_transcribed(transcript)

    assert results == {"dummy": {"words": 3, "note": "Dummy saw it."}}
    assert notes == ["Dummy saw it."]
    assert dummy.seen == [transcript]


def test_a_failing_or_malformed_hook_becomes_a_note_and_the_others_still_run():
    class Raises(V1Extension):
        name = "raises"

        def on_file_transcribed(self, transcript):
            raise RuntimeError("kaput")

    class WrongType(V1Extension):
        name = "wrongtype"

        def on_file_transcribed(self, transcript):
            return ["not", "a", "dict"]

    class NotJson(V1Extension):
        name = "notjson"

        def on_file_transcribed(self, transcript):
            return {"x": object()}

    class Fine(V1Extension):
        name = "fine"

        def on_file_transcribed(self, transcript):
            return {"ok": True}

    class Silent(V1Extension):
        name = "silent"

    reg = ExtensionRegistry()
    for cls in (Raises, WrongType, NotJson, Fine, Silent):
        reg.add(cls)

    results, notes = reg.file_transcribed(FileTranscript(1, "t", "en", 1, "a.wav"))

    assert results == {"fine": {"ok": True}}
    assert len(notes) == 3
    assert all(n.startswith("Extension ") for n in notes)
    assert "kaput" in notes[0]


def test_an_upload_runs_the_hook_on_the_stored_transcription(ext_client, db, dummy, sample_wav_bytes, monkeypatch):
    stub_transcription(monkeypatch)

    job = upload(ext_client, sample_wav_bytes)

    assert job["state"] == "done", job["error"]
    result = job["result"]
    assert result["extensions"] == {"dummy": {"words": 4, "note": "Dummy saw it."}}
    assert "Dummy saw it." in result["notes"]
    (seen,) = dummy.seen
    assert seen.id == result["id"]
    assert seen.text == "hello from the file"
    assert seen.original_filename == "meeting.wav"
    assert seen.words == (Word("hello", 0.0, 0.4), Word("from", 0.4, 0.6, 1))
    assert db.get_file_transcription(seen.id).text == seen.text


def test_a_hook_that_raises_never_fails_the_upload(db, session_factory, sample_wav_bytes, monkeypatch):
    class Exploding(V1Extension):
        name = "exploding"

        def on_file_transcribed(self, transcript):
            raise RuntimeError("kaput")

    reg = ExtensionRegistry()
    reg.add(Exploding)
    api = DashboardAPI(session_factory, Config(), db=db, extensions=reg)
    app = _create_app(api)
    stub_transcription(monkeypatch)

    with app.test_client() as client:
        job = upload(client, sample_wav_bytes)

    assert job["state"] == "done", job["error"]
    assert job["result"]["extensions"] == {}
    assert any("kaput" in n for n in job["result"]["notes"])
    assert len(db.get_file_transcriptions()) == 1


def test_without_extensions_an_upload_adds_nothing_but_an_empty_extensions_dict(
    flask_client, sample_wav_bytes, monkeypatch
):
    stub_transcription(monkeypatch)

    job = upload(flask_client, sample_wav_bytes)

    assert job["result"]["extensions"] == {}
    assert job["result"]["notes"] == []
    assert flask_client.get("/api/extensions").get_json() == {
        "api_version": API_VERSION,
        "extensions": [],
        "panels": [],
        "exports": [],
        "upload_actions": [],
    }


# ---- Hook: dashboard panels --------------------------------------------------


def test_the_dashboard_lists_the_panels_and_exports(ext_client):
    info = ext_client.get("/api/extensions").get_json()

    assert info["extensions"] == [{"name": "dummy", "version": "1.2.3"}]
    assert info["panels"] == [{"id": "dummy-tab", "title": "Dummy", "script": "/ext/dummy-tab/panel.js"}]
    assert info["exports"] == [{"id": "dummy-csv", "label": "Dummy CSV", "extension": ".csv", "mimetype": "text/csv"}]


def test_a_panel_serves_its_static_files_and_its_blueprint(ext_client):
    script = ext_client.get("/ext/dummy-tab/panel.js")

    assert script.status_code == 200 and b"registerPanel" in script.data
    assert ext_client.get("/api/ext/dummy-tab/hello").get_json() == {"hello": "world"}


def test_panel_static_files_stay_inside_the_panel_folder(ext_client, tmp_path):
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    assert ext_client.get("/ext/dummy-tab/../secret.txt").status_code == 404
    assert ext_client.get("/ext/dummy-tab/missing.js").status_code == 404
    assert ext_client.get("/ext/no-such-panel/panel.js").status_code == 404


def test_a_clashing_blueprint_does_not_take_the_dashboard_down(db, session_factory, tmp_path):
    def blueprint():
        bp = Blueprint("same_name", __name__)
        bp.add_url_rule("/x", "x", lambda: "x")
        return bp

    class Clash(V1Extension):
        name = "clash"

        def dashboard_panels(self):
            return [
                DashboardPanel("first", "First", tmp_path, blueprint=blueprint()),
                DashboardPanel("second", "Second", tmp_path, blueprint=blueprint()),
            ]

    reg = ExtensionRegistry()
    reg.add(Clash)
    app = _create_app(DashboardAPI(session_factory, Config(), db=db, extensions=reg))

    with app.test_client() as client:
        assert client.get("/api/extensions").status_code == 200
        assert client.get("/api/ext/first/x").data == b"x"


# ---- Hook: export targets ----------------------------------------------------


def test_an_export_target_downloads_a_stored_transcription(ext_client, db):
    record = db.save_file_transcription("hello world", "en", 1000, "Team Call.wav")

    resp = ext_client.get(f"/api/file-transcriptions/{record.id}/export/dummy-csv")

    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert resp.data == f"id,text\n{record.id},hello world\n".encode()
    assert 'filename="Team Call.csv"' in resp.headers["Content-Disposition"]


def test_an_export_download_name_survives_non_ascii(ext_client, db):
    record = db.save_file_transcription("hallo", "de", 1000, "Büro-Besprechung.wav")

    resp = ext_client.get(f"/api/file-transcriptions/{record.id}/export/dummy-csv")

    disposition = resp.headers["Content-Disposition"]
    assert "filename*=UTF-8''B%C3%BCro-Besprechung.csv" in disposition
    disposition.encode("latin-1")  # a header must be latin-1 clean


def test_export_errors_are_json_not_crashes(db, session_factory, ext_client):
    record = db.save_file_transcription("x", "en", 1, "a.wav")

    assert ext_client.get(f"/api/file-transcriptions/{record.id}/export/nope").status_code == 404
    assert ext_client.get("/api/file-transcriptions/9999/export/dummy-csv").status_code == 404


def test_an_export_target_that_raises_gives_a_500_with_a_message(db, session_factory):
    def explode(transcript):
        raise RuntimeError("template missing")

    class Broken(V1Extension):
        name = "broken"

        def export_targets(self):
            return [ExportTarget("broken", "Broken", ".x", "text/plain", explode)]

    reg = ExtensionRegistry()
    reg.add(Broken)
    app = _create_app(DashboardAPI(session_factory, Config(), db=db, extensions=reg))
    record = db.save_file_transcription("x", "en", 1, "a.wav")

    with app.test_client() as client:
        resp = client.get(f"/api/file-transcriptions/{record.id}/export/broken")

    assert resp.status_code == 500
    assert "template missing" in resp.get_json()["error"]


def test_an_installed_distribution_is_discovered_through_importlib_metadata(tmp_path, monkeypatch):
    """The real lookup, with a throw-away distribution on sys.path instead of a fake entry point."""
    (tmp_path / "fake_ext_pkg.py").write_text(
        "from nexusvox.extensions import Extension\n\n\n"
        "class Installed(Extension):\n    api_version = 1\n    name = 'installed'\n",
        encoding="utf-8",
    )
    dist = tmp_path / "fake_ext_pkg-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: fake-ext-pkg\nVersion: 1.0\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text(
        "[nexusvox.extensions]\ninstalled = fake_ext_pkg:Installed\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(__import__("sys").modules, "fake_ext_pkg", raising=False)

    reg = ExtensionRegistry()
    reg.load()

    assert [e.name for e in reg.extensions] == ["installed"]


# ---- Review fixes -------------------------------------------------------------


def test_an_extension_that_does_not_declare_its_api_version_is_skipped(caplog):
    class Undeclared(Extension):
        name = "undeclared"

    assert Extension.api_version is None
    assert ExtensionRegistry().add(Undeclared) is False
    assert "undeclared" in caplog.text


def test_an_extension_instance_with_call_is_registered_not_called():
    class Callable_(V1Extension):
        name = "callable"

        def __call__(self):
            return None

    reg = ExtensionRegistry()

    assert reg.add(Callable_()) is True
    assert [e.name for e in reg.extensions] == ["callable"]


def test_a_hook_result_the_json_provider_cannot_sort_becomes_a_note():
    class MixedKeys(V1Extension):
        name = "mixed"

        def on_file_transcribed(self, transcript):
            return {1: "a", "note": "x"}

    reg = ExtensionRegistry()
    reg.add(MixedKeys)

    results, notes = reg.file_transcribed(FileTranscript(1, "t", "en", 1, "a.wav"))

    assert results == {}
    assert len(notes) == 1 and notes[0].startswith("Extension mixed failed")


def test_a_relative_or_missing_static_dir_drops_the_panel(tmp_path):
    class Folders(V1Extension):
        name = "folders"

        def dashboard_panels(self):
            return [
                DashboardPanel("relative", "Relative", Path("static")),
                DashboardPanel("missing", "Missing", tmp_path / "nope"),
                DashboardPanel("fine", "Fine", tmp_path),
            ]

    reg = ExtensionRegistry()
    reg.add(Folders)

    assert [p.id for p in reg.panels()] == ["fine"]


def test_an_export_target_with_an_unusable_suffix_or_type_is_dropped():
    def target(id_, extension=".csv", mimetype="text/csv"):
        return ExportTarget(id_, "Label", extension, mimetype, lambda t: "")

    class Suffixes(V1Extension):
        name = "suffixes"

        def export_targets(self):
            return [
                target("slash", ".tar/gz"),
                target("newline", ".csv\r\nX-Evil: 1"),
                target("nodot", "csv"),
                target("badtype", mimetype="text/csv\r\nX: 1"),
                target("good", ".tar.gz", "application/gzip"),
            ]

    reg = ExtensionRegistry()
    reg.add(Suffixes)

    assert [t.id for t in reg.export_targets()] == ["good"]


def test_an_extension_asking_for_the_registry_while_it_loads_does_not_deadlock(monkeypatch):
    registry = ExtensionRegistry()
    monkeypatch.setattr(ext, "_default", registry)
    monkeypatch.setattr(ext, "_default_loaded", False)
    seen = []

    class Nosy(V1Extension):
        name = "nosy"

        def __init__(self):
            seen.append(ext.load_extensions())  # the same thread: a reentrant lock lets it through

    monkeypatch.setattr(ext, "entry_points", lambda **kw: [fake_entry_point("nosy", Nosy)])
    done = []
    thread = threading.Thread(target=lambda: done.append(ext.load_extensions()), daemon=True)
    thread.start()
    thread.join(5)

    assert done == [registry] and seen == [registry]
    assert [e.name for e in registry.extensions] == ["nosy"]


def test_a_failure_after_the_save_does_not_fail_the_upload(db, session_factory, sample_wav_bytes, monkeypatch):
    class Fine(V1Extension):
        name = "fine"

    reg = ExtensionRegistry()
    reg.add(Fine)
    api = DashboardAPI(session_factory, Config(), db=db, extensions=reg)
    app = _create_app(api)
    stub_transcription(monkeypatch)

    def broken(record):
        raise ValueError("odd row")

    monkeypatch.setattr("nexusvox.dashboard.api.file_transcript_from_record", broken)

    with app.test_client() as client:
        job = upload(client, sample_wav_bytes)

    assert job["state"] == "done", job["error"]
    assert job["result"]["extensions"] == {}
    assert any("odd row" in n for n in job["result"]["notes"])
    assert len(db.get_file_transcriptions()) == 1


def test_a_download_name_with_control_characters_and_slashes_stays_a_valid_header(ext_client, db):
    record = db.save_file_transcription("x", "en", 1, "a\r\nb/c.wav")

    resp = ext_client.get(f"/api/file-transcriptions/{record.id}/export/dummy-csv")

    assert resp.status_code == 200
    disposition = resp.headers["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition


# ---- API version 2: export availability and upload actions -----------------------


def test_an_extension_that_ignores_the_additions_behaves_as_before():
    class Old(Extension):
        name = "old"
        api_version = 1

        def export_targets(self):
            return [ExportTarget("old-txt", "Old TXT", ".txt", "text/plain", lambda t: t.text)]

    reg = ExtensionRegistry()

    assert reg.add(Old) is True
    assert reg.unavailable_exports(1) == {}
    assert [t.id for t in reg.export_targets()] == ["old-txt"]


def _gated_registry(answer):
    class Gated(V1Extension):
        name = "gated"

        def export_targets(self):
            return [ExportTarget("gated-md", "Summary", ".md", "text/markdown", lambda t: "x", answer)]

    reg = ExtensionRegistry()
    assert reg.add(Gated)
    return reg


def test_unavailable_exports_carry_their_reason():
    assert _gated_registry(lambda tid: "No summary yet").unavailable_exports(1) == {"gated-md": "No summary yet"}
    assert _gated_registry(lambda tid: False).unavailable_exports(1) == {"gated-md": "Not available"}
    assert _gated_registry(lambda tid: True).unavailable_exports(1) == {}
    assert _gated_registry(lambda tid: 0).unavailable_exports(1) == {"gated-md": "Not available"}
    assert _gated_registry(lambda tid: None).unavailable_exports(1) == {}


def test_a_failing_availability_check_still_offers_the_export():
    def boom(tid):
        raise RuntimeError("db gone")

    assert _gated_registry(boom).unavailable_exports(1) == {}


def test_the_upload_list_marks_unavailable_exports(db, session_factory):
    reg = _gated_registry(lambda tid: "No summary yet" if tid == 1 else True)
    first = db.save_file_transcription("one", "en", 1000, "a.wav")
    second = db.save_file_transcription("two", "en", 1000, "b.wav")
    api = DashboardAPI(session_factory, Config(), db=db, extensions=reg)
    app = _create_app(api)

    rows = {r["id"]: r for r in app.test_client().get("/api/file-transcriptions").get_json()}

    assert rows[first.id]["exports_unavailable"] == {"gated-md": "No summary yet"}
    assert rows[second.id]["exports_unavailable"] == {}


class ActionExtension(V1Extension):
    name = "actions"

    def __init__(self, static_dir):
        self._static = static_dir

    def dashboard_panels(self):
        return [DashboardPanel("x", "X", self._static)]

    def upload_actions(self):
        return [
            UploadAction("summarise", "Summarise", endpoint="/api/ext/x/summarise/{id}"),
            UploadAction("scripted", "Scripted", panel="x"),
            UploadAction("typo", "Typo", panel="y"),
            UploadAction("backslash", "Backslash", endpoint=r"/\evil.example/{id}"),
            UploadAction("both", "Both", endpoint="/api/x", panel="x"),
            UploadAction("elsewhere", "Elsewhere", endpoint="https://example.com/{id}"),
        ]


def test_upload_actions_are_listed_and_malformed_ones_dropped(tmp_path):
    reg = ExtensionRegistry()
    assert reg.add(ActionExtension(tmp_path))

    assert [a.id for a in reg.upload_actions()] == ["summarise", "scripted"]
    assert reg.describe()["upload_actions"] == [
        {"id": "summarise", "label": "Summarise", "endpoint": "/api/ext/x/summarise/{id}", "panel": ""},
        {"id": "scripted", "label": "Scripted", "endpoint": "", "panel": "x"},
    ]


def test_the_dashboard_lists_upload_actions(db, session_factory, tmp_path):
    reg = ExtensionRegistry()
    reg.add(ActionExtension(tmp_path))
    app = _create_app(DashboardAPI(session_factory, Config(), db=db, extensions=reg))

    info = app.test_client().get("/api/extensions").get_json()

    assert info["api_version"] == API_VERSION
    assert [a["id"] for a in info["upload_actions"]] == ["summarise", "scripted"]
