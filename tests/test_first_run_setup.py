"""The first-run setup: when it is due, the dictation microphone, the level meter and the page's steps."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexusvox.audio import devices
from nexusvox.audio import meter as meter_module
from nexusvox.config import EXAMPLE_CONFIG_PATH, AudioConfig, Config, load_config, save_config

STATIC = Path(__file__).resolve().parents[1] / "src" / "nexusvox" / "dashboard" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SETUP_JS = (STATIC / "dashboard-setup.js").read_text(encoding="utf-8")
AWKWARD = 'Mic "Ä" (USB) \\ 1\n'


# ── When the setup is due ────────────────────────────────────────────


def test_a_config_without_the_key_counts_as_set_up(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[general]\ninjection_delay_ms = 300\n", encoding="utf-8")
    assert load_config(path).setup_done is True
    assert Config().setup_done is True


def test_the_template_is_the_only_config_that_asks_for_the_setup(tmp_path):
    path = tmp_path / "config.toml"  # does not exist: load_config creates it from the template
    assert load_config(path).setup_done is False
    assert "setup_done = false" in path.read_text(encoding="utf-8")
    assert EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8").count("setup_done") >= 1


def test_setup_done_is_kept_in_the_file(tmp_path):
    path = tmp_path / "config.toml"
    config = Config(setup_done=False)
    save_config(config, path)
    assert load_config(path).setup_done is False
    config.setup_done = True
    save_config(config, path)
    assert load_config(path).setup_done is True


def test_the_microphone_name_is_kept_in_the_file(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(audio=AudioConfig(device=AWKWARD)), path)
    assert load_config(path).audio.device == AWKWARD
    assert AudioConfig().device == ""


def test_the_api_reports_whether_the_setup_is_due(flask_client):
    assert flask_client.get("/api/setup").get_json() == {"needed": False}
    assert flask_client.get("/api/settings").get_json()["setup_done"] is True


def test_finishing_or_skipping_ends_the_setup(db, session_factory, monkeypatch):
    from nexusvox.dashboard import _create_app
    from nexusvox.dashboard.api import DashboardAPI

    saved: list[bool] = []
    monkeypatch.setattr("nexusvox.dashboard.api_setup.save_config", lambda cfg, path=None: saved.append(cfg.setup_done))
    config = Config(setup_done=False)
    app = _create_app(DashboardAPI(session_factory, config, db=db))
    app.config["TESTING"] = True
    with app.test_client() as client:
        assert client.get("/api/setup").get_json() == {"needed": True}
        reply = client.post("/api/setup/complete", json={}).get_json()
        assert reply["setup_done"] is True
        assert client.get("/api/setup").get_json() == {"needed": False}
    assert config.setup_done is True and saved == [True]


# ── The microphone ───────────────────────────────────────────────────


def test_choosing_a_microphone_is_saved_and_shown_in_the_settings(flask_client):
    reply = flask_client.post("/api/setup/microphone", json={"device": "USB Mic"})
    assert reply.status_code == 200 and reply.get_json()["audio_device"] == "USB Mic"
    assert flask_client.post("/api/setup/microphone", json={"device": ""}).get_json()["audio_device"] == ""


def test_an_absurd_device_name_is_refused(flask_client):
    reply = flask_client.post("/api/setup/microphone", json={"device": "x" * 500})
    assert reply.status_code == 400 and "too long" in reply.get_json()["error"]


def test_the_microphone_list_names_the_chosen_one(flask_client, monkeypatch):
    fake = types.SimpleNamespace(
        query_hostapis=lambda: [{"name": "Windows WASAPI"}],
        query_devices=lambda kind=None: [{"name": "USB Mic", "max_input_channels": 1, "hostapi": 0, "index": 0}],
        _terminate=lambda: None,
        _initialize=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    flask_client.post("/api/setup/microphone", json={"device": "USB Mic"})
    assert flask_client.get("/api/setup/microphones").get_json() == {"inputs": ["USB Mic"], "device": "USB Mic"}


def _device(name: str, inputs: int, hostapi: int, index: int, rate: int) -> dict:
    return {
        "name": name,
        "max_input_channels": inputs,
        "hostapi": hostapi,
        "index": index,
        "default_samplerate": rate,
    }


class FakeSd:
    """Two host APIs: MME (names cut at 31 characters) and WASAPI (full names)."""

    DEVICES = [
        _device("Microphone (USB Audio Dev", 1, 0, 0, 44100),
        _device("Microphone (USB Audio Device)", 1, 1, 1, 48000),
        _device("Speakers", 0, 1, 2, 48000),
    ]

    @staticmethod
    def query_hostapis():
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]

    @classmethod
    def query_devices(cls, kind=None):
        return cls.DEVICES[0] if kind == "input" else cls.DEVICES

    @staticmethod
    def _terminate():
        pass

    @staticmethod
    def _initialize():
        pass


def test_a_named_microphone_is_found_on_wasapi_and_unnamed_means_the_default():
    assert devices.find_input_device(FakeSd, "Microphone (USB Audio Device)")["index"] == 1
    assert devices.find_input_device(FakeSd, "")["index"] == 0
    assert devices.find_input_device(FakeSd, "Unplugged Mic")["index"] == 0  # gone: the default input
    assert devices.list_input_names(FakeSd) == ["Microphone (USB Audio Device)"]


def test_dictation_records_from_the_chosen_microphone(monkeypatch):
    capture_module = pytest.importorskip("nexusvox.audio.capture")
    opened: list[dict] = []
    monkeypatch.setattr(
        capture_module,
        "sd",
        types.SimpleNamespace(
            InputStream=lambda **kw: opened.append(kw) or MagicMock(),
            query_devices=FakeSd.query_devices,
            query_hostapis=FakeSd.query_hostapis,
            _terminate=FakeSd._terminate,
            _initialize=FakeSd._initialize,
            WasapiSettings=lambda **kw: ("wasapi", kw),
        ),
    )
    config = AudioConfig(device="Microphone (USB Audio Device)")
    import asyncio

    async def record() -> None:
        capture = capture_module.AudioCapture(config)
        capture.start()
        capture.stop()
        config.device = ""  # the dashboard changes it while the app runs: read again at the next recording
        capture.start()
        capture.stop()

    asyncio.run(record())
    assert opened[0]["device"] == 1 and opened[0]["extra_settings"] == ("wasapi", {"auto_convert": True})
    assert opened[1]["device"] is None and "extra_settings" not in opened[1]


# ── The level meter ──────────────────────────────────────────────────


def test_the_level_is_on_a_decibel_scale():
    assert meter_module.level_from_rms(0.0) == 0.0
    assert meter_module.level_from_rms(1e-5) == 0.0  # below -60 dB
    assert meter_module.level_from_rms(1.0) == 1.0
    assert meter_module.level_from_rms(0.1) == pytest.approx(2 / 3)  # -20 dB


class FakeStream:
    def __init__(self, **kw) -> None:
        self.kw = kw
        self.callback = kw["callback"]
        self.closed = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _meter_with_fake_stream(monkeypatch, streams: list[FakeStream]):
    import numpy as np

    sd = types.SimpleNamespace(
        InputStream=lambda **kw: streams.append(FakeStream(**kw)) or streams[-1],
        query_devices=FakeSd.query_devices,
        query_hostapis=FakeSd.query_hostapis,
        _terminate=FakeSd._terminate,
        _initialize=FakeSd._initialize,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return np


def test_the_meter_reads_the_loudest_chunk_since_the_last_read_and_closes(monkeypatch):
    from nexusvox.audio import state as audio_state

    streams: list[FakeStream] = []
    np = _meter_with_fake_stream(monkeypatch, streams)
    meter = meter_module.LevelMeter()
    try:
        assert meter.read("Microphone (USB Audio Device)") == {"level": 0.0}  # opens the stream
        assert audio_state.streams_open() == 1
        assert streams[0].kw["device"] == 1 and streams[0].kw["samplerate"] == 48000
        streams[0].callback(np.full((100, 1), 0.1, dtype="float32"), 100, None, None)
        streams[0].callback(np.full((100, 1), 0.01, dtype="float32"), 100, None, None)
        assert meter.read("Microphone (USB Audio Device)")["level"] == pytest.approx(2 / 3, abs=0.01)
        assert meter.read("Microphone (USB Audio Device)")["level"] == 0.0  # the peak starts again
        assert len(streams) == 1  # the same stream served all three reads
    finally:
        meter.close()
    assert streams[0].closed and audio_state.streams_open() == 0


def test_the_meter_follows_a_changed_microphone(monkeypatch):
    streams: list[FakeStream] = []
    _meter_with_fake_stream(monkeypatch, streams)
    meter = meter_module.LevelMeter()
    try:
        meter.read("Microphone (USB Audio Device)")
        meter.read("")
        assert len(streams) == 2 and streams[0].closed and streams[1].kw["device"] == 0
    finally:
        meter.close()


def test_a_microphone_that_cannot_be_opened_is_reported_not_raised(monkeypatch):
    def broken(**kw):
        raise OSError("device busy")

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        types.SimpleNamespace(
            InputStream=broken,
            query_devices=FakeSd.query_devices,
            query_hostapis=FakeSd.query_hostapis,
            _terminate=FakeSd._terminate,
            _initialize=FakeSd._initialize,
        ),
    )
    from nexusvox.audio import state as audio_state

    reading = meter_module.LevelMeter().read("")
    assert reading["level"] == 0.0 and "could not be opened" in reading["error"]
    assert audio_state.streams_open() == 0


def test_the_level_and_stop_routes_use_the_meter(flask_client, monkeypatch):
    fake = MagicMock()
    fake.read.return_value = {"level": 0.5}
    api = next(
        cell.cell_contents
        for fn in flask_client.application.view_functions.values()
        for cell in (fn.__closure__ or ())
        if type(cell.cell_contents).__name__ == "DashboardAPI"
    )
    api._level_meter = fake
    flask_client.post("/api/setup/microphone", json={"device": "USB Mic"})
    assert flask_client.get("/api/setup/mic-level").get_json() == {"level": 0.5}
    fake.read.assert_called_once_with("USB Mic")
    assert flask_client.post("/api/setup/mic-level/stop").get_json() == {"ok": True}
    fake.close.assert_called_once()


# ── The page ─────────────────────────────────────────────────────────


def test_the_setup_has_four_steps_in_this_order_and_a_skip_on_every_one():
    steps = re.findall(r'<li data-step="(\d)">([^<]+)</li>', HTML)
    assert steps == [("0", "Welcome"), ("1", "Microphone"), ("2", "Hotkey"), ("3", "Ready")]
    assert len(re.findall(r'class="setup-panel" data-step="\d"', HTML)) == 4
    # One skip link below the panels, not inside one: it stays on screen on every step.
    assert HTML.index('id="setup-skip"') > HTML.rindex('class="setup-panel"')


def test_the_microphone_step_has_the_device_choice_and_a_live_meter():
    step = HTML[HTML.index('data-step="1" aria-labelledby') : HTML.index('data-step="2" aria-labelledby')]
    assert 'id="setup-mic-select"' in step and 'role="meter"' in step and 'id="setup-meter-bar"' in step


def test_the_hotkey_step_offers_the_choices_and_the_hello_world_test():
    step = HTML[HTML.index('data-step="2" aria-labelledby') : HTML.index('data-step="3" aria-labelledby')]
    assert "Use other keys" in step
    assert re.findall(r'name="setup-mode" value="(\w+)"', step) == ["hold", "toggle"]
    assert set(re.findall(r'name="setup-feedback" value="(\w+)"', step)) == {"visual", "sound", "both"}
    assert 'id="setup-test"' in step and "Hello world" in step


def test_ready_leads_to_home():
    assert 'id="setup-finish">Open Home</button>' in HTML
    assert 'goToTab("home")' in SETUP_JS


def test_the_setup_covers_the_dashboard_while_it_is_open():
    assert 'document.querySelector("main").inert = true' in SETUP_JS
    assert 'role="dialog" aria-modal="true"' in HTML
