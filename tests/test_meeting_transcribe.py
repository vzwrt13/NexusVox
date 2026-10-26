"""Meeting transcript tests: header repair, echo filter, merge, mix balance, and the end-to-end job."""

from __future__ import annotations

import json
import struct
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from nexusvox.config import Config
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.diarization import format_transcript
from nexusvox.extensions import Extension, ExtensionRegistry
from nexusvox.meeting_transcribe import (
    MAX_GAIN,
    PEAK_LIMIT,
    TARGET_RMS,
    balance_gain,
    drop_echo,
    load_meeting,
    merge_words,
    mix_tracks,
    repair_wav_header,
    speech_rms,
    track_to_wav_chunks,
)
from nexusvox.transcriber import TranscriptionResult, Word


def write_wav(path: Path, samples: np.ndarray, rate: int = 16_000, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.astype("<i2").tobytes())


def tone(seconds: float, amplitude: float, rate: int = 16_000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def zero_header(path: Path) -> None:
    """What a killed recorder leaves: the placeholder header `wave` writes at open, never patched."""
    raw = bytearray(path.read_bytes())
    struct.pack_into("<I", raw, 4, 36)
    struct.pack_into("<I", raw, 40, 0)
    path.write_bytes(bytes(raw))


def write_meeting(folder: Path, mic: np.ndarray, system: np.ndarray, *, ended: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    write_wav(folder / "mic.wav", mic)
    write_wav(folder / "system.wav", system)
    meta = {
        "version": 1,
        "started_at": "2026-10-26T10:00:00+00:00",
        "ended_at": "2026-10-26T10:01:00+00:00" if ended else None,
        "tracks": {
            "mic": {"file": "mic.wav", "sample_rate": 16000, "channels": 1, "sample_width": 2},
            "system": {"file": "system.wav", "sample_rate": 16000, "channels": 1, "sample_width": 2},
        },
    }
    (folder / "meeting.json").write_text(json.dumps(meta), encoding="utf-8")


# ---- Recovery ---------------------------------------------------------------


def test_truncated_header_is_repaired_from_the_file_size(tmp_path):
    path = tmp_path / "mic.wav"
    samples = tone(2, 1000)
    write_wav(path, samples)
    zero_header(path)
    with pytest.raises(wave.Error), wave.open(str(path), "rb") as wf:  # the broken file reads as empty
        assert wf.getnframes() == 0
        raise wave.Error

    assert repair_wav_header(path) is True

    with wave.open(str(path), "rb") as wf:
        assert wf.getnframes() == len(samples)
        assert np.array_equal(np.frombuffer(wf.readframes(len(samples)), dtype="<i2"), samples)
    assert repair_wav_header(path) is False  # a good file is left alone


def test_partial_last_frame_of_a_killed_recording_is_cut(tmp_path):
    path = tmp_path / "mic.wav"
    write_wav(path, tone(1, 1000))
    zero_header(path)
    with path.open("ab") as f:
        f.write(b"\x01")  # the process died between the two bytes of a frame

    repair_wav_header(path)

    with wave.open(str(path), "rb") as wf:
        assert wf.getnframes() == 16_000


def test_interrupted_meeting_is_loaded_with_repaired_tracks(tmp_path):
    folder = tmp_path / "meeting-1"
    write_meeting(folder, tone(3, 1000), tone(3, 1000), ended=False)
    zero_header(folder / "mic.wav")
    zero_header(folder / "system.wav")

    meeting = load_meeting(folder)

    assert meeting.interrupted
    assert {t.frames for t in meeting.tracks.values()} == {48_000}
    assert any("interrupted" in n for n in meeting.notes)
    assert any("repaired" in n for n in meeting.notes)


def test_finished_meeting_is_not_interrupted(tmp_path):
    write_meeting(tmp_path / "m", tone(1, 1000), tone(1, 1000))
    meeting = load_meeting(tmp_path / "m")
    assert not meeting.interrupted
    assert meeting.notes == []


def test_missing_track_is_left_out_with_a_note(tmp_path):
    folder = tmp_path / "m"
    write_meeting(folder, tone(1, 1000), tone(1, 1000))
    (folder / "system.wav").unlink()

    meeting = load_meeting(folder)

    assert list(meeting.tracks) == ["mic"]
    assert any("Others" in n for n in meeting.notes)


# ---- Conversion -------------------------------------------------------------


def test_track_is_resampled_to_16k_mono_chunks(tmp_path):
    path = tmp_path / "mic.wav"
    stereo = np.repeat(tone(130, 2000, rate=48_000), 2)  # 130 s, 48 kHz, two identical channels
    write_wav(path, stereo, rate=48_000, channels=2)
    folder = tmp_path / "m"
    write_meeting(folder, tone(1, 1), tone(1, 1))
    (folder / "mic.wav").write_bytes(path.read_bytes())
    meta = json.loads((folder / "meeting.json").read_text())
    meta["tracks"]["mic"].update(sample_rate=48_000, channels=2)
    (folder / "meeting.json").write_text(json.dumps(meta))
    track = load_meeting(folder).tracks["mic"]

    chunks, arrays = track_to_wav_chunks(track)

    assert [len(a) for a in arrays] == [960_000, 960_000, 160_000]
    with wave.open(__import__("io").BytesIO(chunks[0]), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels()) == (16_000, 1)


# ---- Echo filter and merge --------------------------------------------------


def w(text, start, end=None):
    return Word(text, start, start + 0.3 if end is None else end)


def test_echo_of_others_is_dropped_from_me():
    others = [w("let's", 1.0), w("start", 1.4), w("with", 1.8), w("the", 2.1), w("budget", 2.4)]
    # The microphone hears the speakers a moment later, and the engine spells one word slightly differently.
    echo = [w("lets", 1.2), w("start", 1.55), w("with", 1.95), w("the", 2.25), w("budgets", 2.55)]
    mine = [w("sounds", 3.5), w("good", 3.9)]

    kept = drop_echo(sorted(echo + mine, key=lambda x: x.start), others)

    assert [x.word for x in kept] == ["sounds", "good"]


def test_speech_over_others_with_other_words_is_kept():
    others = [w("we", 1.0), w("need", 1.3), w("more", 1.6), w("time", 1.9)]
    interjection = [w("yes", 1.4), w("agreed", 1.8)]  # talking at the same time, not an echo

    assert drop_echo(interjection, others) == interjection


def test_same_word_much_later_is_not_an_echo():
    others = [w("okay", 1.0)]
    later = [w("okay", 30.0)]

    assert drop_echo(later, others) == later


def test_merge_orders_by_time_and_labels_me_and_others():
    me = [w("hi", 0.0), w("there", 0.4), w("fine", 5.0), w("thanks", 5.4)]
    others = [w("how", 1.0), w("are", 1.4), w("you", 1.8)]

    merged = merge_words(me, others)

    assert [x.word for x in merged] == ["hi", "there", "how", "are", "you", "fine", "thanks"]
    assert [x.speaker for x in merged] == [1, 1, 2, 2, 2, 1, 1]
    from nexusvox.meeting_transcribe import format_meeting

    assert format_meeting(merged) == "Me: hi there\n\nOthers: how are you\n\nMe: fine thanks"


def test_format_transcript_without_names_is_unchanged():
    assert format_transcript([Word("hi", 0, 1, 1), Word("yo", 1, 2, 2)]) == "Speaker 1: hi\n\nSpeaker 2: yo"


# ---- Mix balance ------------------------------------------------------------


def test_mix_balances_a_quiet_microphone_against_loud_system_audio(tmp_path):
    quiet = [tone(10, 900)]  # the user's voice, far below the system audio
    loud = [tone(10, 12_000)]
    quiet_before, loud_before = quiet[0].copy(), loud[0].copy()
    out = tmp_path / "meeting.wav"

    mix_tracks([quiet, loud], out)

    assert np.array_equal(quiet[0], quiet_before) and np.array_equal(loud[0], loud_before)  # inputs unchanged
    assert balance_gain(quiet) > 3 > balance_gain(loud)
    assert speech_rms([quiet[0] * balance_gain(quiet)]) == pytest.approx(TARGET_RMS, rel=0.05)
    with wave.open(str(out), "rb") as wf:
        mixed = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    assert len(mixed) == 160_000
    assert np.abs(mixed).max() <= PEAK_LIMIT + 1  # does not clip


def test_each_track_alone_in_the_mix_reaches_the_target_level():
    for amplitude in (900, 3000, 12_000):
        track = [tone(5, amplitude)]
        assert speech_rms([track[0].astype(np.float32) * balance_gain(track)]) == pytest.approx(TARGET_RMS, rel=0.05)


def test_near_silent_track_is_not_boosted_into_noise():
    hiss = [(np.random.default_rng(1).normal(0, 20, 160_000)).astype(np.int16)]  # below the noise floor

    assert speech_rms(hiss) == 0.0
    assert balance_gain(hiss) == 1.0
    faint = [tone(5, 150)]  # audible but very quiet: boosted, never past the cap
    assert balance_gain(faint) == MAX_GAIN


def test_pauses_do_not_lower_a_tracks_speech_level():
    talking = tone(2, 3000)
    padded = [np.concatenate([talking, np.zeros(16_000 * 18, dtype=np.int16)])]

    assert speech_rms(padded) == pytest.approx(speech_rms([talking]), rel=0.05)


def test_mix_pads_the_shorter_track(tmp_path):
    out = tmp_path / "m.wav"

    seconds = mix_tracks([[tone(2, 3000)], [tone(3, 3000)]], out)

    assert seconds == pytest.approx(3.0)


def test_loud_overlapping_tracks_do_not_clip(tmp_path):
    out = tmp_path / "m.wav"
    mix_tracks([[tone(2, 30_000)], [tone(2, 30_000)]], out)
    with wave.open(str(out), "rb") as wf:
        mixed = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    assert np.abs(mixed).max() <= 32_767


# ---- The job ----------------------------------------------------------------


class Recorder(Extension):
    name = "recorder"
    api_version = 1

    def __init__(self):
        self.seen = []

    def on_file_transcribed(self, transcript):
        self.seen.append(transcript)


@pytest.fixture
def meeting_api(db, session_factory, tmp_path, monkeypatch):
    registry = ExtensionRegistry()
    hook = Recorder()
    assert registry.add(hook)
    config = Config()
    config.database.path = str(db._engine.url.database)
    api = DashboardAPI(session_factory, config, db=db, extensions=registry)

    def fake_transcribe(chunks, **kwargs):
        # The first call is the microphone, the second the system audio (load order of the tracks).
        calls = getattr(fake_transcribe, "calls", 0)
        fake_transcribe.calls = calls + 1
        result = TranscriptionResult()
        if calls == 0:
            result.words = [w("sounds", 3.0), w("good", 3.4)]
        else:
            result.words = [w("hello", 1.0), w("everyone", 1.4)]
        result.finalize(text=" ".join(x.word for x in result.words))
        return result

    monkeypatch.setattr(api, "_transcribe_wav_chunks", fake_transcribe)
    return api, hook


def test_transcribe_meeting_stores_one_me_others_transcript_and_a_mix(meeting_api, db, tmp_path):
    api, hook = meeting_api
    folder = tmp_path / "meeting-20261026-100000"
    write_meeting(folder, tone(5, 300), tone(5, 12_000))
    zero_header(folder / "mic.wav")  # killed before the header was written
    meta = json.loads((folder / "meeting.json").read_text())
    meta["ended_at"] = None
    (folder / "meeting.json").write_text(json.dumps(meta))
    mic_bytes = (folder / "mic.wav").read_bytes()

    result = api.transcribe_meeting(folder)

    assert result["ok"], result
    assert result["kind"] == "meeting"
    assert result["text"] == "Others: hello everyone\n\nMe: sounds good"
    assert any("interrupted" in n for n in result["notes"])
    row = db.get_file_transcription(result["id"])
    assert row.kind == "meeting"
    stored = json.loads(row.words)
    assert [(x["word"], x["speaker"]) for x in stored] == [("hello", 2), ("everyone", 2), ("sounds", 1), ("good", 1)]
    assert (folder / "meeting.wav").is_file()
    assert api.get_file_audio_path(row.id) == str((folder / "meeting.wav").resolve())
    assert row.duration_ms == 5000
    assert hook.seen[0].kind == "meeting"
    # The repaired header is the only change the recorded tracks get.
    assert (folder / "mic.wav").read_bytes()[44:] == mic_bytes[44:]
    listed = api.get_file_transcriptions()[0]
    assert (listed["kind"], listed["has_audio"]) == ("meeting", True)


def test_start_meeting_transcription_runs_as_a_background_job(meeting_api, tmp_path):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    started = api.start_meeting_transcription(folder)

    assert started["ok"]
    deadline = time.monotonic() + 5
    while (job := api.get_file_job(started["job_id"]))["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job["state"] == "done"
    assert job["result"]["kind"] == "meeting"


def test_meeting_without_readable_tracks_is_an_error(meeting_api, tmp_path):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(1, 1000), tone(1, 1000))
    (folder / "mic.wav").unlink()
    (folder / "system.wav").unlink()

    assert api.transcribe_meeting(folder)["ok"] is False


def test_uploads_keep_kind_upload(db):
    row = db.save_file_transcription(text="x", language="en", duration_ms=1, original_filename="a.wav")
    assert (row.kind, row.audio_path) == ("upload", None)
