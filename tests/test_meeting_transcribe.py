"""Meeting transcript tests: header repair, echo filter, merge, mix balance, and the end-to-end job."""

from __future__ import annotations

import io
import json
import os
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
    ECHO_MIN_RUN,
    MAX_GAIN,
    PEAK_LIMIT,
    TARGET_RMS,
    balance_gain,
    convert_track,
    drop_dictation,
    drop_echo,
    load_meeting,
    merge_words,
    mix_tracks,
    repair_wav_header,
    speech_rms,
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


def zero_header(path: Path, *, age_s: float = 3600) -> None:
    """What a killed recorder leaves: the placeholder header `wave` writes at open, never patched.

    The file is also made `age_s` old: a recording that is still running looks the same but is fresh.
    """
    raw = bytearray(path.read_bytes())
    struct.pack_into("<I", raw, 4, 36)
    struct.pack_into("<I", raw, 40, 0)
    path.write_bytes(bytes(raw))
    old = time.time() - age_s
    os.utime(path, (old, old))


def wav16(tmp_path: Path, name: str, samples: np.ndarray) -> Path:
    path = tmp_path / f"{name}.wav"
    write_wav(path, samples)
    return path


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
    with path.open("ab") as f:
        f.write(b"\x01")  # the process died between the two bytes of a frame
    zero_header(path)

    repair_wav_header(path)

    with wave.open(str(path), "rb") as wf:
        assert wf.getnframes() == 16_000


def test_header_of_a_file_written_a_moment_ago_is_not_touched(tmp_path):
    path = tmp_path / "mic.wav"
    write_wav(path, tone(1, 1000))
    zero_header(path, age_s=0)  # same header as a recording that is still running
    before = path.read_bytes()

    with pytest.raises(ValueError, match="still be recording"):
        repair_wav_header(path)

    assert path.read_bytes() == before


def test_meeting_still_recording_is_refused_and_its_files_stay_as_they_are(tmp_path):
    folder = tmp_path / "live"
    write_meeting(folder, tone(1, 1000), tone(1, 1000), ended=False)
    zero_header(folder / "mic.wav", age_s=0)
    zero_header(folder / "system.wav", age_s=0)
    before = [(folder / n).read_bytes() for n in ("mic.wav", "system.wav")]

    with pytest.raises(ValueError, match="still be recording"):
        load_meeting(folder)

    assert [(folder / n).read_bytes() for n in ("mic.wav", "system.wav")] == before


def test_valid_file_is_never_opened_for_writing(tmp_path):
    path = tmp_path / "mic.wav"
    write_wav(path, tone(1, 1000))
    path.chmod(0o444)
    try:
        assert repair_wav_header(path) is False
    finally:
        path.chmod(0o666)


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

    out = tmp_path / "mic16k.wav"
    chunks = convert_track(track, out)

    assert len(chunks) == 3
    with wave.open(io.BytesIO(chunks[0]), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getnframes()) == (16_000, 1, 960_000)
    with wave.open(str(out), "rb") as wf:  # the file the mix reads is the same audio
        assert (wf.getframerate(), wf.getnchannels(), wf.getnframes()) == (16_000, 1, 2_080_000)


# ---- Echo filter and merge --------------------------------------------------


def w(text, start, end=None):
    return Word(text, start, start + 0.3 if end is None else end)


def run(words, start, step=0.4):
    return [w(x, start + i * step) for i, x in enumerate(words)]


def test_echo_of_others_is_dropped_from_me():
    others = run(["let's", "start", "with", "the", "budget"], 1.0)
    # The microphone hears the speakers a moment later, and the engine spells one word slightly differently.
    echo = run(["lets", "start", "with", "the", "budgets"], 1.2)
    mine = run(["sounds", "good"], 3.5)

    kept = drop_echo(sorted(echo + mine, key=lambda x: x.start), others)

    assert [x.word for x in kept] == ["sounds", "good"]


def test_german_echo_is_dropped_including_umlauts_and_eszett():
    others = run(["Das", "Budget", "ist", "größer", "als", "gedacht"], 1.0)
    echo = run(["das", "Budget", "ist", "grösser", "als", "gedacht."], 1.15)  # casing, ss for ß, a full stop

    assert drop_echo(echo, others) == []


def test_german_crosstalk_with_similar_short_words_is_kept():
    others = run(["dass", "wir", "für", "morgen"], 1.0)
    mine = run(["das", "mir", "für", "heute"], 1.1)  # das/dass, mir/wir: one or two look-alikes, not a run

    assert drop_echo(mine, others) == mine


def test_short_words_said_at_the_same_time_are_kept():
    others = run(["they", "okay", "yes"], 1.0)
    mine = run(["the", "okay"], 1.1)  # the/they and okay/okay are alike, but two words are not an echo

    assert len(mine) < ECHO_MIN_RUN
    assert drop_echo(mine, others) == mine


def test_a_run_of_look_alike_words_broken_by_a_real_word_is_kept():
    others = run(["we", "can", "ship", "it", "today"], 1.0)
    mine = [w("we", 1.1), w("can", 1.5), w("never", 1.9), w("it", 2.3), w("today", 2.7)]

    assert [x.word for x in drop_echo(mine, others)] == ["we", "can", "never", "it", "today"]


def test_speech_over_others_with_other_words_is_kept():
    others = run(["we", "need", "more", "time"], 1.0, step=0.3)
    interjection = [w("yes", 1.4), w("agreed", 1.8)]  # talking at the same time, not an echo

    assert drop_echo(interjection, others) == interjection


def test_same_words_much_later_are_not_an_echo():
    others = run(["okay", "so", "then"], 1.0)
    later = run(["okay", "so", "then"], 30.0)

    assert drop_echo(later, others) == later


def test_echo_filter_stays_fast_on_a_long_meeting():
    words = [f"word{i}" for i in range(8000)]  # about an hour of speech per track
    others = [w(x, i * 0.45) for i, x in enumerate(words)]
    me = [w(x, i * 0.45 + 0.2) for i, x in enumerate(words)]
    started = time.perf_counter()

    kept = drop_echo(me, others)

    assert kept == []
    assert time.perf_counter() - started < 15


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


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")


def test_mix_balances_a_quiet_microphone_against_loud_system_audio(tmp_path):
    quiet = tone(10, 900)  # the user's voice, far below the system audio
    loud = tone(10, 12_000)
    quiet_path, loud_path = wav16(tmp_path, "mic", quiet), wav16(tmp_path, "system", loud)
    quiet_bytes, loud_bytes = quiet_path.read_bytes(), loud_path.read_bytes()
    out = tmp_path / "meeting.wav"

    mix_tracks([quiet_path, loud_path], out)

    assert quiet_path.read_bytes() == quiet_bytes and loud_path.read_bytes() == loud_bytes  # inputs unchanged
    assert balance_gain([quiet]) > 3 > balance_gain([loud])
    assert speech_rms([quiet * balance_gain([quiet])]) == pytest.approx(TARGET_RMS, rel=0.05)
    mixed = read_wav(out)
    assert len(mixed) == 160_000
    assert np.abs(mixed).max() <= PEAK_LIMIT + 1  # does not clip


def test_each_track_alone_in_the_mix_reaches_the_target_level(tmp_path):
    for amplitude in (900, 3000, 12_000):
        out = tmp_path / f"m{amplitude}.wav"
        mix_tracks([wav16(tmp_path, f"t{amplitude}", tone(5, amplitude))], out)
        assert speech_rms([read_wav(out)]) == pytest.approx(TARGET_RMS, rel=0.05)


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
    seconds = mix_tracks([wav16(tmp_path, "a", tone(2, 3000)), wav16(tmp_path, "b", tone(3, 3000))], tmp_path / "m.wav")

    assert seconds == pytest.approx(3.0)


def test_loud_overlapping_tracks_do_not_clip(tmp_path):
    out = tmp_path / "m.wav"
    mix_tracks([wav16(tmp_path, "a", tone(2, 30_000)), wav16(tmp_path, "b", tone(2, 30_000))], out)
    assert np.abs(read_wav(out)).max() <= PEAK_LIMIT + 1


def test_failed_mix_leaves_no_partial_file(tmp_path, monkeypatch):
    out = tmp_path / "meeting.wav"
    paths = [wav16(tmp_path, "a", tone(130, 3000)), wav16(tmp_path, "b", tone(130, 3000))]
    real = np.zeros
    calls = []

    def failing_zeros(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:  # the second window: the first one is already on disk
            raise OSError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr("nexusvox.meeting_transcribe.np.zeros", failing_zeros)

    with pytest.raises(OSError):
        mix_tracks(paths, out)

    assert not out.exists() and not list(tmp_path.glob("*.tmp"))


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
        result.confidence = 0.5 + calls * 0.25
        if calls == 0:
            result.words = [w("sounds", 3.0), w("good", 3.4)]
        else:
            result.words = [w("hello", 1.0), w("everyone", 1.4)]
        result.finalize(text=" ".join(x.word for x in result.words))
        return result

    monkeypatch.setattr(api, "_transcribe_wav_chunks", fake_transcribe)
    return api, hook


def test_text_without_timing_is_not_lost_when_only_some_words_are_timed(meeting_api, tmp_path, monkeypatch):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    def partly_timed(chunks, **kwargs):
        result = TranscriptionResult()
        result.words = [w("hello", 1.0)]  # the server could not time "everyone"
        result.finalize(text="hello everyone")
        return result

    monkeypatch.setattr(api, "_transcribe_wav_chunks", partly_timed)

    result = api.transcribe_meeting(folder)

    assert result["ok"]
    assert result["text"] == "Me: hello everyone\n\nOthers: hello everyone"
    assert any("incomplete" in n for n in result["notes"])


def test_failed_mix_is_a_note_and_the_meeting_is_still_stored(meeting_api, db, tmp_path, monkeypatch):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))
    monkeypatch.setattr("nexusvox.dashboard.api.mix_tracks", lambda paths, out: (_ for _ in ()).throw(OSError("full")))

    result = api.transcribe_meeting(folder)

    assert result["ok"] and any("mix could not be written" in n for n in result["notes"])
    assert db.get_file_transcription(result["id"]).audio_path is None
    assert not (folder / "meeting.wav").exists()


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
    assert row.confidence == pytest.approx(0.625)  # the mean of the two tracks
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


# ---- Leaving dictation out --------------------------------------------------


def test_words_touching_a_dictation_take_are_dropped_with_a_margin():
    me = [w("before", 1.0), w("near", 4.2), w("inside", 6.0), w("after", 9.0), w("late", 12.0)]

    kept = drop_dictation(me, [(5.0, 8.0)])

    # "near" ends at 4.5, inside the 0.5 s margin before the take; "after" starts at 9.0, beyond the one after it.
    assert [x.word for x in kept] == ["before", "after", "late"]


def test_german_and_english_dictation_words_are_dropped_alike():
    for words, keep in ((["send", "the", "report", "now"], "thanks"), (["schick", "den", "Bericht", "jetzt"], "danke")):
        me = run(words, 10.0) + [w(keep, 30.0)]
        assert [x.word for x in drop_dictation(me, [(10.0, 11.6)])] == [keep]


def test_dictation_ranges_that_overlap_or_nest_all_count():
    me = [w("a", 1.0), w("b", 5.0), w("c", 9.0), w("d", 20.0)]

    kept = drop_dictation(me, [(0.0, 10.0), (4.0, 5.0), (8.5, 9.5)])

    assert [x.word for x in kept] == ["d"]


def test_no_dictation_ranges_keeps_every_word():
    me = run(["one", "two"], 1.0)
    assert drop_dictation(me, []) == me


def test_merge_words_only_drops_dictation_when_given_ranges():
    me = run(["note", "this"], 5.0) + [w("later", 20.0)]
    others = run(["hello"], 1.0)

    assert [x.word for x in merge_words(me, others)] == ["hello", "note", "this", "later"]
    assert [x.word for x in merge_words(me, others, [(5.0, 6.0)])] == ["hello", "later"]


def test_load_meeting_reads_dictation_ranges_and_skips_broken_entries(tmp_path):
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))
    meta = json.loads((folder / "meeting.json").read_text())
    meta["dictations"] = [
        {"start_s": 5, "end_s": 6.5},
        {"start_s": "x"},
        {"start_s": 9, "end_s": 8},
        {"start_s": 1, "end_s": 2},
    ]
    (folder / "meeting.json").write_text(json.dumps(meta))

    assert load_meeting(folder).dictations == [(1.0, 2.0), (5.0, 6.5)]


def test_meeting_without_dictation_ranges_loads_as_before(tmp_path):
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))
    assert load_meeting(folder).dictations == []


def _dictation_meeting(folder: Path, ranges: list[tuple[float, float]]) -> None:
    write_meeting(folder, tone(2, 3000), tone(2, 3000))
    meta = json.loads((folder / "meeting.json").read_text())
    meta["dictations"] = [{"start_s": s, "end_s": e} for s, e in ranges]
    (folder / "meeting.json").write_text(json.dumps(meta))


@pytest.mark.parametrize(
    ("spoken", "dictated", "kept_text"),
    [
        (("sounds", "good", "thanks"), ("sounds", "good"), "Me: thanks"),
        (("klingt", "gut", "danke"), ("klingt", "gut"), "Me: danke"),
    ],
)
def test_switch_decides_whether_dictation_stays_in_the_transcript(meeting_api, tmp_path, spoken, dictated, kept_text):
    api, _ = meeting_api
    folder = tmp_path / "m"
    _dictation_meeting(folder, [(2.9, 3.6)])
    mic_audio = (folder / "mic.wav").read_bytes()

    def fake(chunks, **kwargs):
        fake.calls = getattr(fake, "calls", 0) + 1
        result = TranscriptionResult()
        if fake.calls % 2:  # microphone first
            result.words = [w(spoken[0], 3.0), w(spoken[1], 3.4), w(spoken[2], 10.0)]
        else:
            result.words = [w("hello", 1.0)]
        result.finalize(text=" ".join(x.word for x in result.words))
        return result

    api._transcribe_wav_chunks = fake

    api._config.meeting.leave_dictation_out = True
    left_out = api.transcribe_meeting(folder)
    assert left_out["text"] == f"Others: hello\n\n{kept_text}"
    assert all(word not in left_out["text"] for word in dictated)

    api._config.meeting.leave_dictation_out = False  # applied at transcription time, so the same folder works again
    kept_in = api.transcribe_meeting(folder)
    assert kept_in["text"] == f"Others: hello\n\nMe: {' '.join(spoken)}"

    assert (folder / "mic.wav").read_bytes() == mic_audio  # the tracks never change


def test_missing_word_timing_is_noted_when_dictation_cannot_be_left_out(meeting_api, tmp_path):
    api, _ = meeting_api
    folder = tmp_path / "m"
    _dictation_meeting(folder, [(0.5, 1.0)])

    def untimed(chunks, **kwargs):
        result = TranscriptionResult()
        result.finalize(text="hello there")
        return result

    api._transcribe_wav_chunks = untimed

    result = api.transcribe_meeting(folder)

    assert any("Dictation could not be left out" in n for n in result["notes"])
