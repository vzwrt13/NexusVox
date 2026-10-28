"""Live transcript of a running meeting: windows, silence, backlog, recorder tap, session and dashboard."""

from __future__ import annotations

import io
import threading
import time
import wave
from datetime import UTC, datetime

import numpy as np
import pytest

from nexusvox.config import MeetingConfig
from nexusvox.meeting_live import LiveTranscriber, _block_rms
from nexusvox.meeting_recorder import AudioFormat, MeetingRecorder
from nexusvox.meeting_session import MeetingSession

RATE = 16_000


def _tone(seconds: float, amplitude: int = 3000, rate: int = RATE, channels: int = 1) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    mono = (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2")
    return np.repeat(mono, channels).tobytes()


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _live(transcribe, window_s=1.0):
    live = LiveTranscriber(transcribe, window_s=window_s)
    live.start()
    return live


def test_each_track_is_sent_in_windows_and_labelled_with_speaker_numbers():
    sent: list[float] = []

    def transcribe(wav: bytes) -> str:
        with wave.open(io.BytesIO(wav)) as w:
            assert (w.getframerate(), w.getnchannels()) == (16_000, 1)
            sent.append(w.getnframes() / 16_000)
        return f"window {len(sent)}"

    live = _live(transcribe)
    fmt = AudioFormat(RATE, 1)
    live.feed("mic", fmt, _tone(1.2))
    live.feed("system", AudioFormat(48_000, 2), _tone(1.2, rate=48_000, channels=2))
    assert _wait(lambda: len(live.segments_since()) == 2)
    live.stop()
    by_speaker = {s["speaker"]: s for s in live.segments_since()}
    assert by_speaker["mic"]["label"] == "Speaker 1" and by_speaker["system"]["label"] == "Speaker 2"
    assert all(0.66 < s <= 1.0 + 1e-6 for s in sent)  # cut at or before the window length, at a quiet block
    assert [s["n"] for s in live.segments_since()] == [0, 1]
    assert live.segments_since(1)[0]["n"] == 1


def test_a_silent_window_is_not_sent():
    calls: list[bytes] = []
    live = _live(lambda wav: calls.append(wav) or "x")
    live.feed("mic", AudioFormat(RATE, 1), bytes(RATE * 2 * 3))  # 3 s of digital silence: three windows
    assert _wait(lambda: live._queue.empty() and live._pending["mic"].frames < RATE)
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    assert _wait(lambda: live.segments_since())
    live.stop()
    assert len(calls) == 1 and live.status()["windows"] == 1  # only the window with speech reached the model


def test_the_cut_lands_in_a_pause_not_inside_a_word():
    live = LiveTranscriber(lambda wav: "x", window_s=1.0)
    pcm = np.frombuffer(_tone(1.5, 4000), dtype="<i2").copy()
    pcm[int(0.8 * RATE) : int(0.85 * RATE)] = 0  # a 50 ms pause inside the window's last third
    from nexusvox.meeting_live import _Pending

    pending = _Pending(RATE)
    pending.add(pcm, 1.5)
    cut = live._cut(pending)
    assert 0.8 * RATE <= cut <= 0.85 * RATE + RATE * 0.05


def test_a_failing_window_is_lost_and_the_next_one_works():
    calls = []

    def transcribe(wav: bytes) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("server down")
        return "back again"

    live = _live(transcribe)
    live.feed("mic", AudioFormat(RATE, 1), _tone(2.5))
    assert _wait(lambda: live.segments_since())
    live.stop()
    assert live.segments_since()[0]["text"] == "back again"
    assert live.status()["windows"] >= 2


def test_a_track_that_falls_behind_drops_its_oldest_audio():
    release = threading.Event()
    live = _live(lambda wav: release.wait(5) and "late")
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    assert _wait(lambda: live.status()["windows"] == 0 and not live._queue.qsize())
    for _ in range(40):  # 40 s while the first window is stuck in the model
        live.feed("mic", AudioFormat(RATE, 1), _tone(1.0))
    release.set()
    assert _wait(lambda: live.status()["skipped_s"] > 0)
    live.stop()


def test_feeding_never_blocks_and_stop_ends_the_worker():
    live = _live(lambda wav: "x")
    live.stop()
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.0))  # ignored after stop
    assert not live.active


def test_block_rms_of_short_audio_is_empty():
    assert len(_block_rms(np.zeros(10, dtype="<i2"), 800)) == 0


# -- recorder tap, session ------------------------------------------------------------------------


class _Source:
    def __init__(self, fmt):
        self.fmt, self.sink = fmt, None

    def describe(self):
        return self.fmt

    def start(self, sink):
        self.sink = sink

    def stop(self):
        pass


def test_the_recorder_hands_every_block_to_the_tap_and_still_records(tmp_path):
    mic, system = _Source(AudioFormat(16_000, 1)), _Source(AudioFormat(48_000, 2))
    rec = MeetingRecorder(tmp_path, mic, system, now=lambda: datetime(2026, 10, 28, 9, 0, tzinfo=UTC))
    seen = []
    rec.set_tap(lambda name, fmt, data: seen.append((name, fmt.sample_rate, len(data))))
    folder = rec.start()
    mic.sink(b"\x01\x00" * 160)
    system.sink(b"\x01\x00" * 960)
    rec.flush()
    rec.stop()
    assert ("mic", 16_000, 320) in seen and ("system", 48_000, 1920) in seen
    assert (folder / "mic.wav").is_file()


def test_a_raising_tap_never_costs_a_recorded_block(tmp_path):
    mic, system = _Source(AudioFormat(16_000, 1)), _Source(AudioFormat(16_000, 1))
    clock = iter(x * 0.01 for x in range(1000))
    rec = MeetingRecorder(
        tmp_path, mic, system, clock=lambda: next(clock), now=lambda: datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    )

    def boom(name, fmt, data):
        raise RuntimeError("tap")

    rec.set_tap(boom)
    folder = rec.start()
    mic.sink(b"\x01\x00" * 160)
    rec.flush()
    rec.stop()
    with wave.open(str(folder / "mic.wav")) as w:
        assert w.getnframes() >= 160


class _Recorder:
    def __init__(self):
        self.tap = None
        self.folder = None

    def set_tap(self, tap):
        self.tap = tap

    def start(self):
        from pathlib import Path

        self.folder = Path("meeting-x")
        return self.folder

    def stop(self):
        return self.folder


def _session(live_transcript: bool, recorder: _Recorder):
    live = LiveTranscriber(lambda wav: "hello", window_s=1.0)
    config = MeetingConfig(live_transcript=live_transcript)
    session = MeetingSession(
        config, lambda: None, recorder_factory=lambda base, cfg: recorder, live_factory=lambda: live
    )
    return session, live


def test_session_feeds_the_live_view_and_ends_it_at_stop():
    rec = _Recorder()
    session, live = _session(True, rec)
    session.start()
    rec.tap("mic", AudioFormat(RATE, 1), _tone(1.3))
    assert _wait(lambda: session.live()["segments"])
    out = session.live()
    assert out["recording"] and out["segments"][0]["label"] == "Speaker 1" and out["status"]["active"]
    session.stop()
    after = session.live()
    assert not after["recording"] and after["segments"] and not live.active  # kept until the next meeting


def test_session_with_live_transcript_off_runs_nothing():
    rec = _Recorder()
    session, live = _session(False, rec)
    session.start()
    assert rec.tap is None and not live.active
    assert session.live() == {"enabled": False, "recording": True, "segments": [], "status": None}
    session.stop()


@pytest.mark.parametrize("enabled", [True, False])
def test_config_round_trips_live_transcript(tmp_path, enabled):
    from nexusvox.config import Config, load_config, save_config

    cfg = Config()
    cfg.meeting.live_transcript = enabled
    save_config(cfg, tmp_path / "c.toml")
    assert load_config(tmp_path / "c.toml").meeting.live_transcript is enabled


def test_labels_follow_the_own_voice_setting_and_never_say_me_or_others():
    live = LiveTranscriber(lambda wav: "hi", window_s=1.0, labels=lambda: {"mic": "Vincent", "system": "Speaker 2"})
    live.start()
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    live.feed("system", AudioFormat(RATE, 1), _tone(1.2))
    assert _wait(lambda: len(live.segments_since()) == 2)
    live.stop()
    assert {s["label"] for s in live.segments_since()} == {"Vincent", "Speaker 2"}


# -- review fixes ---------------------------------------------------------------------------------


def test_blocks_fed_before_start_are_timed_from_the_meeting_start():
    now = [5000.0]
    live = LiveTranscriber(lambda wav: "hi", clock=lambda: now[0], window_s=1.0)  # created = meeting clock zero
    now[0] += 0.5
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))  # the recorder feeds before the worker starts
    live.start()
    assert _wait(lambda: live.segments_since())
    live.stop()
    assert live.segments_since()[0]["at_s"] <= 1  # not ~5000


def test_audio_fed_during_a_slow_window_is_kept_and_the_backlog_limit_applies(monkeypatch):
    monkeypatch.setattr("nexusvox.meeting_live.MAX_BACKLOG_S", 10.0)
    release = threading.Event()
    texts = iter(["first", "second", "third", "fourth"])
    live = _live(lambda wav: release.wait(5) and next(texts))
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    assert _wait(lambda: live._queue.empty())
    for _ in range(300):  # 30 s of 100 ms blocks, far more than the old 2000-block queue could hold for two tracks
        live.feed("mic", AudioFormat(RATE, 1), _tone(0.1))
        live.feed("system", AudioFormat(RATE, 1), _tone(0.1))
    release.set()
    assert _wait(lambda: live.status()["skipped_s"] > 0)
    live.stop()
    assert live.status()["skipped_s"] > 15  # whole audio was seen by the trim, not dropped block by block at random


def test_the_track_waiting_longest_goes_first():
    order = []
    live = LiveTranscriber(lambda wav: order.append(1) or "x", window_s=1.0)
    live.feed("system", AudioFormat(RATE, 1), _tone(1.2))
    live._drain()
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    live._drain()
    assert live._ready()[0] == "system"


def test_a_dictation_take_holds_back_new_windows():
    taking = [True]
    live = LiveTranscriber(lambda wav: "x", window_s=1.0)
    live.pause_while(lambda: taking[0])
    live.start()
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    time.sleep(0.6)
    assert live.segments_since() == []
    taking[0] = False
    assert _wait(lambda: live.segments_since())
    live.stop()


class _Transcriber:
    def __init__(self, done=True, text="hi"):
        self.done, self.text, self.connects, self.disconnects, self.made = done, text, 0, 0, 0
        self.loops = set()

    async def connect(self):
        import asyncio

        self.loops.add(id(asyncio.get_running_loop()))
        self.connects += 1

    async def send_audio_chunk(self, pcm):
        pass

    async def finish(self, timeout=30.0):
        from nexusvox.transcriber import TranscriptionResult

        result = TranscriptionResult()
        result.finalize(text=self.text)
        result.done = self.done
        return result

    async def disconnect(self):
        self.disconnects += 1


def test_window_transcriber_reuses_one_transcriber_and_loop_and_closes_them():
    from nexusvox.meeting_live import WindowTranscriber
    from nexusvox.meeting_transcribe import _wav_bytes

    made = []
    t = _Transcriber()
    wt = WindowTranscriber(lambda: made.append(1) or t)
    wav = _wav_bytes(np.zeros(1600, dtype=np.int16))
    assert wt(wav) == "hi" and wt(wav) == "hi"
    assert len(made) == 1 and t.connects == 2 and len(t.loops) == 1
    wt.close()
    assert t.disconnects == 1


def test_a_window_the_server_did_not_finish_is_an_error_not_a_silent_success():
    from nexusvox.meeting_live import WindowTranscriber
    from nexusvox.meeting_transcribe import _wav_bytes

    wt = WindowTranscriber(lambda: _Transcriber(done=False, text=""))
    with pytest.raises(TimeoutError):
        wt(_wav_bytes(np.zeros(1600, dtype=np.int16)))
    wt.close()

    live = _live(wt_raising := (lambda wav: (_ for _ in ()).throw(TimeoutError("slow"))))
    live.feed("mic", AudioFormat(RATE, 1), _tone(1.2))
    assert _wait(lambda: live.status()["error"])
    live.stop()
    assert "slow" in live.status()["error"] and wt_raising


def test_the_worker_closes_its_transcriber_when_it_ends():
    closed = []

    class Fn:
        def __call__(self, wav):
            return "x"

        def close(self):
            closed.append(1)

    live = _live(Fn())
    live.stop()
    assert closed == [1]


def test_stopping_the_session_does_not_hold_the_session_lock():
    rec = _Recorder()
    session, live = _session(True, rec)
    session.start()
    entered, release = threading.Event(), threading.Event()

    def slow_stop(timeout=5.0):
        entered.set()
        release.wait(5)

    live.stop = slow_stop
    stopper = threading.Thread(target=session.stop)
    stopper.start()
    assert entered.wait(5)
    assert session.status()["recording"] is False  # would block while the lock is held
    assert session.begin_dictation(0.0) is None
    release.set()
    stopper.join(5)


def test_live_transcript_is_off_by_default(tmp_path):
    from nexusvox.config import Config, load_config

    assert MeetingConfig().live_transcript is False and Config().meeting.live_transcript is False
    path = tmp_path / "c.toml"
    path.write_text('[meeting]\nkey = "M"\n', encoding="utf-8")  # a file from before the setting existed
    assert load_config(path).meeting.live_transcript is False


def test_the_shipped_config_template_leaves_it_off():
    import tomllib
    from pathlib import Path

    import nexusvox

    template = Path(nexusvox.__file__).parent / "config.example.toml"
    assert tomllib.loads(template.read_text(encoding="utf-8"))["meeting"]["live_transcript"] is False
