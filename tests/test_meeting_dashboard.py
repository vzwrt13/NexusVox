"""Meeting recording from the dashboard and the meeting hotkey, and deleting uploads and meetings."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexusvox.config import Config, MeetingConfig, load_config, save_config, validate_meeting_hotkey
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.meeting_recorder import META_FILENAME
from nexusvox.meeting_session import MeetingSession

# -- config ---------------------------------------------------------------------------------------


def test_meeting_hotkey_default_does_not_start_dictation():
    cfg = Config()
    assert cfg.meeting.modifiers == ["ctrl", "alt"] and cfg.meeting.key == "M"
    assert validate_meeting_hotkey(cfg.meeting.modifiers, cfg.meeting.key, cfg.hotkey) is None


@pytest.mark.parametrize(
    ("modifiers", "key", "fragment"),
    [
        (["ctrl", "alt"], "", "needs a key"),
        ([], "M", "at least one modifier"),
        (["ctrl", "shift", "alt"], "M", "also start dictation"),  # contains the modifier-only dictation chord
        (["ctrl", "alt"], "enter", "Unknown key"),
    ],
)
def test_meeting_hotkey_is_rejected_when_unusable(modifiers, key, fragment):
    assert fragment in validate_meeting_hotkey(modifiers, key, Config().hotkey)


def test_meeting_hotkey_may_not_equal_a_dictation_hotkey_with_a_key():
    cfg = Config()
    cfg.hotkey.modifiers, cfg.hotkey.key = ["ctrl", "alt"], "M"
    assert "dictation hotkey" in validate_meeting_hotkey(["alt", "ctrl"], "m", cfg.hotkey)


def test_meeting_section_round_trips_and_an_unusable_file_falls_back(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config()
    cfg.meeting = MeetingConfig(
        modifiers=["ctrl", "win"], key="F8", mic_device='Mic "A"', output_device="Speakers", consent_hint=False
    )
    save_config(cfg, path)
    loaded = load_config(path).meeting
    assert loaded == cfg.meeting

    path.write_text('[meeting]\nmodifiers = ["ctrl", "shift", "alt"]\nkey = "M"\n', encoding="utf-8")
    assert load_config(path).meeting.modifiers == ["ctrl", "alt"]  # would also start dictation


# -- the meeting hotkey ---------------------------------------------------------------------------

LCTRL, LALT, LSHIFT = 0xA2, 0xA4, 0xA0
VK_M = ord("M")


def _hotkey():
    pytest.importorskip("pynput.keyboard", reason="pynput needs a display backend", exc_type=ImportError)
    from nexusvox.hotkey import MeetingHotkey

    toggles: list[int] = []
    config = MeetingConfig()
    return MeetingHotkey(config, lambda: toggles.append(1)), toggles, config


def test_meeting_hotkey_toggles_once_per_press_and_swallows_its_key():
    hk, toggles, _ = _hotkey()
    hk._handle(LCTRL, True)
    hk._handle(LALT, True)
    assert hk._handle(VK_M, True) is True and toggles == [1]
    assert hk._handle(VK_M, True) is True  # auto-repeat
    assert toggles == [1]
    assert hk._handle(VK_M, False) is True  # key-up of a swallowed key
    hk._handle(VK_M, True)
    assert toggles == [1, 1]


def test_meeting_hotkey_ignores_plain_typing_and_a_partial_chord():
    hk, toggles, _ = _hotkey()
    assert hk._handle(VK_M, True) is False and hk._handle(VK_M, False) is False
    hk._handle(LCTRL, True)
    assert hk._handle(VK_M, True) is False  # Alt is not down
    hk._handle(VK_M, False)
    hk._handle(LCTRL, False)
    assert toggles == []


def test_meeting_hotkey_follows_the_live_config():
    hk, toggles, config = _hotkey()
    config.modifiers, config.key = ["ctrl", "shift"], "F9"
    hk._handle(LCTRL, True)
    hk._handle(LSHIFT, True)
    assert hk._handle(VK_M, True) is False  # the old key is plain typing now
    assert hk._handle(0x78, True) is True  # VK_F9
    assert toggles == [1]


# -- session --------------------------------------------------------------------------------------


class FakeRecorder:
    def __init__(self, base: Path) -> None:
        self.folder = base / "meeting-20261026-100000"
        self.running = False

    def start(self) -> Path:
        self.folder.mkdir(parents=True)
        (self.folder / META_FILENAME).write_text("{}", encoding="utf-8")
        self.running = True
        return self.folder

    def stop(self) -> Path:
        self.running = False
        return self.folder


class Clock:
    t = 50.0

    def __call__(self) -> float:
        return self.t


def _session(tmp_path, clock=None):
    return MeetingSession(
        MeetingConfig(), lambda: tmp_path / "meetings", lambda base, cfg: FakeRecorder(base), clock or Clock()
    )


def test_session_reports_elapsed_time_and_frees_itself_on_stop(tmp_path):
    clock = Clock()
    session = _session(tmp_path, clock)
    assert session.status() == {"recording": False, "elapsed_s": 0, "folder": None}
    folder = session.start()
    clock.t += 125.9
    assert session.status() == {"recording": True, "elapsed_s": 125, "folder": folder.name}
    assert session.stop() == folder
    assert not session.recording


def test_session_refuses_a_second_start_and_a_stop_without_a_meeting(tmp_path):
    session = _session(tmp_path)
    with pytest.raises(RuntimeError):
        session.stop()
    session.start()
    with pytest.raises(RuntimeError):
        session.start()


def test_session_builds_the_recorder_from_the_devices_chosen_at_that_moment(tmp_path):
    seen: list[str] = []
    cfg = MeetingConfig()
    session = MeetingSession(cfg, lambda: tmp_path, lambda base, c: seen.append(c.mic_device) or FakeRecorder(base))
    cfg.mic_device = "Headset"
    session.start()
    assert seen == ["Headset"]


# -- dashboard API --------------------------------------------------------------------------------


@pytest.fixture
def env(db, session_factory, tmp_path, monkeypatch):
    config = Config()
    config.database.path = str(tmp_path / "test.db")
    api = DashboardAPI(session_factory, config, db=db, meeting=_session(tmp_path / "audio"))
    monkeypatch.setattr("nexusvox.dashboard.api.save_config", lambda cfg, path=None: None)
    started: list[Path] = []
    monkeypatch.setattr(
        api, "start_meeting_transcription", lambda folder: started.append(folder) or {"ok": True, "job_id": "j1"}
    )
    app = _create_app(api)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, api, db, tmp_path, started


def test_start_and_stop_through_the_api_start_the_transcription_job(env):
    client, _, _, tmp_path, started = env
    assert client.get("/api/meetings/status").get_json()["recording"] is False

    resp = client.post("/api/meetings/start")
    assert resp.status_code == 200 and resp.get_json()["recording"] is True
    assert client.get("/api/meetings/status").get_json()["recording"] is True

    resp = client.post("/api/meetings/stop")
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] and body["job_id"] == "j1" and body["recording"] is False
    assert started == [tmp_path / "audio" / "meetings" / "meeting-20261026-100000"]


def test_stop_reports_a_busy_transcriber_but_keeps_the_folder_name(env, monkeypatch):
    client, api, _, _, _ = env
    monkeypatch.setattr(
        api, "start_meeting_transcription", lambda f: {"ok": False, "busy": True, "error": "Another file is busy"}
    )
    client.post("/api/meetings/start")
    resp = client.post("/api/meetings/stop")
    assert resp.status_code == 200
    assert resp.get_json()["busy"] is True and resp.get_json()["folder"] == "meeting-20261026-100000"


def test_stop_without_a_meeting_and_a_second_start_are_errors(env):
    client, *_ = env
    assert client.post("/api/meetings/stop").status_code == 500
    client.post("/api/meetings/start")
    assert client.post("/api/meetings/start").status_code == 500


def test_toggle_is_what_the_hotkey_calls(env):
    _, api, _, _, started = env
    assert api.toggle_meeting()["recording"] is True
    assert api.toggle_meeting()["recording"] is False
    assert len(started) == 1


def test_quitting_closes_a_running_meeting(env):
    _, api, *_ = env
    api.start_meeting()
    api.shutdown_meeting()
    assert api.get_meeting_status()["recording"] is False


def test_meeting_settings_endpoints(env):
    client, *_ = env
    settings = client.get("/api/settings").get_json()
    assert settings["meeting_modifiers"] == ["ctrl", "alt"] and settings["meeting_key"] == "M"
    assert settings["meeting_consent_hint"] is True

    resp = client.post("/api/settings/meeting-hotkey", json={"modifiers": ["shift", "ctrl"], "key": "f8"})
    assert resp.status_code == 200
    assert resp.get_json()["meeting_modifiers"] == ["ctrl", "shift"] and resp.get_json()["meeting_key"] == "F8"

    resp = client.post("/api/settings/meeting-hotkey", json={"modifiers": ["ctrl", "shift", "alt"], "key": "M"})
    assert resp.status_code == 400 and "dictation" in resp.get_json()["error"]
    assert client.post("/api/settings/meeting-hotkey", json={"modifiers": "ctrl"}).status_code == 400

    assert (
        client.post("/api/settings/meeting-consent-hint", json={"enabled": False}).get_json()["meeting_consent_hint"]
        is False
    )
    resp = client.post("/api/settings/meeting-devices", json={"mic": "Headset", "output": "Speakers"})
    assert resp.get_json()["meeting_mic_device"] == "Headset"
    assert resp.get_json()["meeting_output_device"] == "Speakers"


def test_devices_endpoint_lists_devices_and_the_chosen_ones(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr("nexusvox.dashboard.api.list_devices", lambda: {"inputs": ["Mic"], "outputs": ["Out"]})
    client.post("/api/settings/meeting-devices", json={"mic": "Mic", "output": ""})
    assert client.get("/api/meetings/devices").get_json() == {
        "inputs": ["Mic"],
        "outputs": ["Out"],
        "mic_device": "Mic",
        "output_device": "",
    }


# -- deleting uploads and meetings ----------------------------------------------------------------


def _meeting_folder(tmp_path, name="meeting-20261026-100000", mix=True):
    folder = tmp_path / "audio" / "meetings" / name
    folder.mkdir(parents=True)
    for f in ("mic.wav", "system.wav", META_FILENAME) + (("meeting.wav",) if mix else ()):
        (folder / f).write_bytes(b"x")
    return folder


def test_list_can_be_narrowed_to_meetings(env):
    client, _, db, *_ = env
    db.save_file_transcription("up", "en", 1000, "a.wav")
    db.save_file_transcription("me", "en", 1000, "meeting-1", kind="meeting")
    assert len(client.get("/api/file-transcriptions").get_json()) == 2
    rows = client.get("/api/file-transcriptions?kind=meeting").get_json()
    assert [r["kind"] for r in rows] == ["meeting"]
    assert len(client.get("/api/file-transcriptions?kind=upload").get_json()) == 1


def test_deleting_a_meeting_removes_the_row_and_the_whole_folder(env):
    client, _, db, tmp_path, _ = env
    folder = _meeting_folder(tmp_path)
    row = db.save_file_transcription(
        "Me: hi", "en", 1000, folder.name, kind="meeting", audio_path="audio/meetings/" + folder.name + "/meeting.wav"
    )

    resp = client.delete(f"/api/file-transcriptions/{row.id}")

    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert not folder.exists()
    assert db.get_file_transcription(row.id) is None
    assert client.get("/api/file-transcriptions").get_json() == []


def test_a_meeting_without_a_mix_is_found_by_its_name(env):
    client, _, db, tmp_path, _ = env
    folder = _meeting_folder(tmp_path, mix=False)
    row = db.save_file_transcription("Me: hi", "en", 1000, folder.name, kind="meeting", audio_path=None)

    assert client.delete(f"/api/file-transcriptions/{row.id}").status_code == 200
    assert not folder.exists()


def test_missing_meeting_files_do_not_block_the_delete(env):
    client, _, db, _, _ = env
    row = db.save_file_transcription(
        "Me: hi", "en", 1000, "meeting-gone", kind="meeting", audio_path="audio/meetings/meeting-gone/meeting.wav"
    )
    assert client.delete(f"/api/file-transcriptions/{row.id}").status_code == 200
    assert db.get_file_transcription(row.id) is None


def test_a_folder_that_is_not_a_meeting_is_never_removed(env):
    client, _, db, tmp_path, _ = env
    other = tmp_path / "audio" / "meetings" / "holiday-photos"
    other.mkdir(parents=True)
    (other / "a.jpg").write_bytes(b"x")
    row = db.save_file_transcription(
        "x", "en", 1000, "holiday-photos", kind="meeting", audio_path="audio/meetings/holiday-photos/meeting.wav"
    )
    assert client.delete(f"/api/file-transcriptions/{row.id}").status_code == 200
    assert (other / "a.jpg").exists()


def test_deleting_an_upload_removes_its_stored_audio(env):
    client, _, db, tmp_path, _ = env
    audio = tmp_path / "audio" / "upload.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"x")
    row = db.save_file_transcription("hello", "en", 1000, "upload.wav", audio_path="audio/upload.wav")

    assert client.delete(f"/api/file-transcriptions/{row.id}").status_code == 200
    assert not audio.exists() and db.get_file_transcription(row.id) is None


def test_deleting_an_upload_without_audio_or_with_a_missing_file(env):
    client, _, db, *_ = env
    plain = db.save_file_transcription("hello", "en", 1000, "a.wav")
    gone = db.save_file_transcription("hello", "en", 1000, "b.wav", audio_path="audio/gone.wav")
    assert client.delete(f"/api/file-transcriptions/{plain.id}").status_code == 200
    assert client.delete(f"/api/file-transcriptions/{gone.id}").status_code == 200
    assert client.get("/api/file-transcriptions").get_json() == []


def test_an_upload_never_deletes_a_file_outside_the_database_folder(env, tmp_path_factory):
    client, _, db, *_ = env
    outside = tmp_path_factory.mktemp("elsewhere") / "keep.wav"
    outside.write_bytes(b"x")
    row = db.save_file_transcription("hello", "en", 1000, "a.wav", audio_path=str(outside))
    assert client.delete(f"/api/file-transcriptions/{row.id}").status_code == 200
    assert outside.exists()


def test_deleting_an_unknown_entry_is_a_404(env):
    client, *_ = env
    resp = client.delete("/api/file-transcriptions/999")
    assert resp.status_code == 404 and "error" in resp.get_json()


def test_the_page_has_a_meetings_tab_and_in_page_delete_confirmation():
    static = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "dashboard.js").read_text(encoding="utf-8")
    assert 'data-tab="meetings"' in html and 'id="meeting-consent"' in html
    assert "Inform all participants before you record" in html
    section = js[js.index("function askDeleteFileTranscription") : js.index("// ── Meetings")]
    assert "confirm(" not in section and "alert(" not in section
