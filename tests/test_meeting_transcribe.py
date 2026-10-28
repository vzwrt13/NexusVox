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
    TURN_SLACK_S,
    MeetingTrack,
    balance_gain,
    convert_track,
    drop_dictation,
    drop_echo,
    echo_lag,
    format_meeting,
    interleave,
    load_meeting,
    loudness_steps,
    merge_words,
    mix_tracks,
    repair_wav_header,
    speech_rms,
    track_offset,
    track_shifts,
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
    assert any("System audio" in n for n in meeting.notes)


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


def test_merge_orders_by_time_and_numbers_the_speakers():
    me = [w("hi", 0.0), w("there", 0.4), w("fine", 5.0), w("thanks", 5.4)]
    others = [w("how", 1.0), w("are", 1.4), w("you", 1.8)]

    merged = merge_words(me, others)

    assert [x.word for x in merged] == ["hi", "there", "how", "are", "you", "fine", "thanks"]
    assert [x.speaker for x in merged] == [1, 1, 2, 2, 2, 1, 1]

    assert format_meeting(merged) == "Speaker 1: hi there\n\nSpeaker 2: how are you\n\nSpeaker 1: fine thanks"
    assert format_meeting(merged, "Alex").startswith("Alex: hi there\n\nSpeaker 2:")


def test_system_speakers_from_detection_follow_the_microphone():
    me = [w("hi", 0.0)]
    others = [Word("yo", 1.0, 1.3, 1), Word("hey", 2.0, 2.3, 2), Word("ok", 3.0, 3.3, 3), w("plain", 4.0)]

    merged = merge_words(me, others)

    assert [x.speaker for x in merged] == [1, 2, 3, 4, 2]


def test_format_transcript_without_names_is_unchanged():
    assert format_transcript([Word("hi", 0, 1, 1), Word("yo", 1, 2, 2)]) == "Speaker 1: hi\n\nSpeaker 2: yo"


# ---- Track offset and turn changes ------------------------------------------


def test_a_tie_at_a_turn_change_does_not_split_the_last_sentence():
    # Word timing from a real meeting: the user finished "... an das Video." and then started the video. The
    # engine started the video's first word on the same 80 ms frame as the user's last word, and let it run on.
    me = [Word("an", 20.32, 20.56), Word("das", 20.56, 20.80), Word("Video.", 20.80, 21.60)]
    others = [Word("Innerhalb", 20.80, 21.36), Word("der", 21.36, 21.52), Word("App", 21.52, 21.84)]

    merged = merge_words(me, others)

    assert format_meeting(merged) == "Speaker 1: an das Video.\n\nSpeaker 2: Innerhalb der App"


def test_a_first_word_timed_early_after_a_pause_waits_for_the_turn_to_end():
    # The engine put the video's first word 0.38 s before its sound, inside the user's last word.
    me = [Word("an", 5.68, 6.00), Word("das", 6.00, 6.24), Word("Video.", 6.24, 7.04)]
    others = [Word("Ich", 6.32, 6.40), Word("würde", 6.56, 6.64), Word("es", 6.80, 6.96)]

    assert format_meeting(merge_words(me, others)) == "Speaker 1: an das Video.\n\nSpeaker 2: Ich würde es"


def test_a_known_track_offset_is_corrected_before_the_tracks_are_merged():
    # The microphone's stream reported 1.05 s of input latency (extreme on purpose: the turn rule alone cannot hide
    # an offset this large), so the system track has the reply 1.0 s before the user heard it. Corrected, the reply
    # ties with the user's last word.
    me = run(["so", "over", "to", "you", "now"], 0.0)  # "now" at 1.6
    others = [Word("thanks", 0.62, 0.9), Word("sure", 1.0, 1.3)]
    meeting = load_meeting_stub(latency={"mic": 1.05, "system": 0.05})

    offset, measured = track_offset(meeting)
    merged = merge_words(me, others, system_offset=offset)

    assert (round(offset, 3), measured) == (1.0, "latency")
    assert format_meeting(merged) == "Speaker 1: so over to you now\n\nSpeaker 2: thanks sure"
    assert [x.start for x in merged if x.speaker == 2] == pytest.approx([1.62, 2.0])
    # Uncorrected, the reply lands inside the user's sentence.
    assert format_meeting(merge_words(me, others)).startswith("Speaker 1: so over to\n\nSpeaker 2: thanks sure")


def test_german_turn_change_with_a_known_offset_keeps_both_sentences_whole():
    me = [Word("Jetzt", 0.0, 0.3), Word("bist", 0.3, 0.5), Word("du", 0.5, 0.7), Word("dran.", 0.7, 1.0)]
    others = [Word("Danke,", 0.4, 0.7), Word("gerne.", 0.7, 1.0)]  # really from 0.75 s: offset 0.35 s

    merged = merge_words(me, others, system_offset=0.35)

    assert format_meeting(merged, "Vincent") == "Vincent: Jetzt bist du dran.\n\nSpeaker 2: Danke, gerne."


def test_a_negative_offset_delays_the_microphone_instead():
    # The system stream reported more latency than the microphone: the microphone is delayed, so no word gets a
    # time before the meeting start, and the dictation ranges (recorded on the microphone's clock) move with it.
    me = [w("dictated", 5.0), w("spoken", 8.0)]
    others = [w("hello", 0.0)]

    merged = merge_words(me, others, [(5.0, 5.4)], system_offset=-1.0)

    assert track_shifts(-1.0) == (1.0, 0.0) and track_shifts(0.25) == (0.0, 0.25)
    assert [(x.word, x.start) for x in merged] == [("hello", 0.0), ("spoken", 9.0)]
    assert min(x.start for x in merge_words([w("hi", 0.0)], others, system_offset=-0.29)) == 0.0


def test_a_turn_ends_after_a_long_pause():
    # The user's last word was 20 s ago: the other side starting at 30.0 s comes before the user's word at 30.3 s.
    me = [w("earlier", 10.0), w("interjection", 30.3)]
    others = [w("hello", 30.0)]

    assert [x.word for x in merge_words(me, others)] == ["earlier", "hello", "interjection"]


def test_a_pause_ends_the_system_tracks_turn_too():
    me = [w("ja", 20.3)]
    others = [w("vorher", 10.0), w("weiter", 20.4)]

    assert [x.word for x in merge_words(me, others)] == ["vorher", "ja", "weiter"]


def test_speakers_on_the_system_track_keep_the_engines_word_order():
    # One transcript: its word order is the spoken one, even where detection changes the speaker on a tie.
    others = [Word("Ende.", 5.0, 5.3, 1), Word("Hallo", 5.0, 5.2, 2), Word("zusammen", 5.2, 5.6, 2)]

    merged = merge_words([], others)

    assert format_meeting(merged) == "Speaker 2: Ende.\n\nSpeaker 3: Hallo zusammen"


def test_crosstalk_is_interleaved_and_no_word_moves_further_than_the_slack():
    me = run([f"m{i}" for i in range(20)], 0.0, step=0.25)
    others = run([f"o{i}" for i in range(20)], 0.1, step=0.25)

    merged = merge_words(me, others)

    assert len(merged) == 40
    assert {x.speaker for x in merged[:10]} == {1, 2}  # both people talk over each other: neither waits it out
    for i, word in enumerate(merged):
        assert all(earlier.start <= word.start + TURN_SLACK_S for earlier in merged[:i])
    for speaker in (1, 2):  # each speaker's own words keep their order
        own = [x.start for x in merged if x.speaker == speaker]
        assert own == sorted(own)


def test_interleave_starts_with_the_earlier_word():
    assert [x.word for x in interleave([w("me", 1.0)], [w("you", 0.9)])] == ["you", "me"]
    assert [x.word for x in interleave([w("me", 1.0)], [w("you", 1.0)])] == ["me", "you"]
    assert [x.word for x in interleave([], [w("you", 1.0)])] == ["you"]


def load_meeting_stub(latency: dict[str, float]):
    from nexusvox.meeting_transcribe import Meeting

    return Meeting(Path("m"), None, False, latency=latency)


def speechlike(seconds: float, seed: int) -> np.ndarray:
    """Noise in bursts of 0.1-0.5 s with pauses between, like speech seen through its loudness."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * 16_000), dtype=np.float32)
    pos = 0
    while pos < len(out):
        burst = int(rng.uniform(0.1, 0.5) * 16_000)
        out[pos : pos + burst] = rng.normal(0, rng.uniform(1000, 6000), len(out[pos : pos + burst]))
        pos += burst + int(rng.uniform(0.05, 0.4) * 16_000)
    return out


def test_echo_of_the_speakers_in_the_microphone_gives_the_offset():
    system = speechlike(60, seed=1)
    delay = int(0.18 * 16_000)
    echo = np.concatenate([np.zeros(delay, dtype=np.float32), system[:-delay]]) * 0.3
    mic = speechlike(60, seed=2) * 0.5 + echo

    offset, measured = track_offset(
        load_meeting_stub({"mic": 0.02, "system": 0.02}), [loudness_steps(mic)], [loudness_steps(system)]
    )

    assert measured == "echo"
    assert offset == pytest.approx(0.18, abs=0.011)


def test_without_echo_the_reported_latencies_are_used():
    # A headset: the microphone hears only the user, who talks while the others are quiet.
    system = speechlike(60, seed=3)
    mic = speechlike(60, seed=4) * (np.abs(system) < 1)
    levels = [loudness_steps(mic)], [loudness_steps(system)]

    assert echo_lag(levels[0][0], levels[1][0]) is None
    offset, measured = track_offset(load_meeting_stub({"mic": 0.026, "system": 0.022}), *levels)
    assert (round(offset, 4), measured) == (0.004, "latency")
    assert track_offset(load_meeting_stub({}), *levels) == (0.0, "none")


def test_silent_or_short_tracks_have_no_echo():
    assert echo_lag(np.zeros(10_000), np.ones(10_000)) is None
    assert echo_lag(np.arange(50.0), np.arange(50.0)) is None
    assert track_offset(load_meeting_stub({}), [], None) == (0.0, "none")


def test_convert_track_collects_the_loudness_steps_in_the_same_pass(tmp_path):
    path = tmp_path / "mic.wav"
    write_wav(path, tone(61, 3000, rate=48_000), rate=48_000)
    track = MeetingTrack("mic", path, 48_000, 1, 61 * 48_000)
    levels: list[np.ndarray] = []

    convert_track(track, tmp_path / "mic16k.wav", levels)

    assert [len(x) for x in levels] == [6000, 100]  # 10 ms steps of each 60 s window
    assert float(np.concatenate(levels).mean()) == pytest.approx(3000 / np.sqrt(2), rel=0.02)


def test_load_meeting_reads_the_reported_latencies(tmp_path):
    folder = tmp_path / "m"
    write_meeting(folder, tone(1, 1000), tone(1, 1000))
    meta = json.loads((folder / "meeting.json").read_text())
    meta["tracks"]["mic"]["latency_s"] = 0.0258
    meta["tracks"]["system"]["latency_s"] = "broken"
    (folder / "meeting.json").write_text(json.dumps(meta))

    assert load_meeting(folder).latency == {"mic": 0.0258}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 5.0, True])
def test_unusable_reported_latencies_are_ignored(tmp_path, value):
    folder = tmp_path / "m"
    write_meeting(folder, tone(1, 1000), tone(1, 1000))
    meta = json.loads((folder / "meeting.json").read_text())
    meta["tracks"]["mic"]["latency_s"] = value  # json writes NaN and Infinity, and reads them back
    meta["tracks"]["system"]["latency_s"] = 0.02
    (folder / "meeting.json").write_text(json.dumps(meta))

    meeting = load_meeting(folder)

    assert meeting.latency == {"system": 0.02}
    assert track_offset(meeting) == (0.0, "none")


def test_mix_delays_a_track_by_its_offset(tmp_path):
    click = np.zeros(3 * 16_000, dtype=np.int16)
    click[16_000 : 16_000 + 1600] = tone(0.1, 8000)
    silent = wav16(tmp_path, "mic", np.zeros(3 * 16_000, dtype=np.int16))
    system = wav16(tmp_path, "system", click)

    for offsets, onset, length in (([0.0, 0.25], 1.25, 3.25), ([0.5, 0.0], 1.0, 3.5), ([0.0, 0.0], 1.0, 3.0)):
        out = tmp_path / "m.wav"
        seconds = mix_tracks([silent, system], out, offsets)
        mixed = read_wav(out)
        assert np.flatnonzero(mixed)[0] / 16_000 == pytest.approx(onset, abs=0.001)
        assert seconds == pytest.approx(length)


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
    assert result["text"] == "Speaker 1: hello everyone\n\nSpeaker 2: hello everyone"
    assert any("incomplete" in n for n in result["notes"])


def test_failed_mix_is_a_note_and_the_meeting_is_still_stored(meeting_api, db, tmp_path, monkeypatch):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))
    monkeypatch.setattr("nexusvox.dashboard.api.mix_tracks", lambda *args: (_ for _ in ()).throw(OSError("full")))

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
    assert result["text"] == "Speaker 2: hello everyone\n\nSpeaker 1: sounds good"
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


def test_the_job_shifts_the_system_track_by_the_recorded_offset(meeting_api, db, tmp_path, monkeypatch):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(5, 3000), np.zeros(5 * 16_000, dtype=np.int16))  # no echo to measure
    meta = json.loads((folder / "meeting.json").read_text())
    meta["tracks"]["mic"]["latency_s"] = 1.05
    meta["tracks"]["system"]["latency_s"] = 0.05
    (folder / "meeting.json").write_text(json.dumps(meta))

    def fake_transcribe(chunks, **kwargs):
        result = TranscriptionResult()
        mic = not hasattr(fake_transcribe, "done")
        fake_transcribe.done = True
        result.words = run(["over", "to", "you", "now"], 0.4) if mic else [w("thanks", 0.9), w("sure", 1.3)]
        result.finalize(text=" ".join(x.word for x in result.words))
        return result

    monkeypatch.setattr(api, "_transcribe_wav_chunks", fake_transcribe)

    result = api.transcribe_meeting(folder)

    assert result["text"] == "Speaker 1: over to you now\n\nSpeaker 2: thanks sure"
    stored = json.loads(db.get_file_transcription(result["id"]).words)
    assert [x["start"] for x in stored if x["speaker"] == 2] == pytest.approx([1.9, 2.3])
    assert db.get_file_transcription(result["id"]).duration_ms == 6000  # the mix moved the system track too


def test_a_failed_alignment_is_a_note_and_the_meeting_is_still_stored(meeting_api, db, tmp_path, monkeypatch):
    api, _ = meeting_api
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    def out_of_memory(*args):
        raise MemoryError("envelope")

    monkeypatch.setattr("nexusvox.meeting_transcribe.echo_lag", out_of_memory)

    result = api.transcribe_meeting(folder)

    assert result["ok"] and any("could not be lined up" in n for n in result["notes"])
    assert result["text"] == "Speaker 2: hello everyone\n\nSpeaker 1: sounds good"
    assert (folder / "meeting.wav").is_file()


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
        (("sounds", "good", "thanks"), ("sounds", "good"), "Speaker 1: thanks"),
        (("klingt", "gut", "danke"), ("klingt", "gut"), "Speaker 1: danke"),
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
    assert left_out["text"] == f"Speaker 2: hello\n\n{kept_text}"
    assert all(word not in left_out["text"] for word in dictated)

    api._config.meeting.leave_dictation_out = False  # applied at transcription time, so the same folder works again
    kept_in = api.transcribe_meeting(folder)
    assert kept_in["text"] == f"Speaker 2: hello\n\nSpeaker 1: {' '.join(spoken)}"

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


def test_dictation_is_dropped_after_the_echo_filter_so_real_speech_is_not_taken_for_echo():
    # Two partnered words, a dictated word, two more partnered words: no run reaches ECHO_MIN_RUN on its own.
    me = [w("alpha", 1.0), w("bravo", 1.4), w("typed", 5.0), w("charlie", 9.0), w("delta", 9.4)]
    others = [w("alpha", 1.0), w("bravo", 1.4), w("charlie", 9.0), w("delta", 9.4)]

    merged = merge_words(me, others, [(4.9, 5.4)])

    assert [x.word for x in merged if x.speaker == 1] == ["alpha", "bravo", "charlie", "delta"]


# ---- Numbered speakers ------------------------------------------------------


def _diarized_system(chunks, **kwargs):
    """Stands in for `_transcribe_wav_chunks`: the microphone first, then a system track with three speakers."""
    _diarized_system.calls = getattr(_diarized_system, "calls", 0) + 1
    result = TranscriptionResult()
    if _diarized_system.calls % 2:
        result.words = [w("sounds", 6.0), w("good", 6.4)]
        result.finalize(text="sounds good")
    else:
        assert kwargs["diarize"] is True
        result.words = [Word("hello", 1.0, 1.3, 1), Word("hi", 2.0, 2.3, 2), Word("hey", 3.0, 3.3, 3)]
        result.finalize(text="Speaker 1: hello\n\nSpeaker 2: hi\n\nSpeaker 3: hey")
        result.plain_text = "hello hi hey"
    return result


def test_system_track_speakers_become_speaker_2_and_up(meeting_api, db, tmp_path):
    api, _ = meeting_api
    _diarized_system.calls = 0
    api._transcribe_wav_chunks = _diarized_system
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert result["text"] == "Speaker 2: hello\n\nSpeaker 3: hi\n\nSpeaker 4: hey\n\nSpeaker 1: sounds good"
    stored = json.loads(db.get_file_transcription(result["id"]).words)
    assert [x["speaker"] for x in stored] == [2, 3, 4, 1, 1]


def test_custom_own_voice_label_is_used_for_the_microphone(meeting_api, tmp_path):
    api, _ = meeting_api
    _diarized_system.calls = 0
    api._transcribe_wav_chunks = _diarized_system
    api._config.meeting.own_voice_label = "Alex"
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert result["text"].endswith("Alex: sounds good")
    assert "Speaker 1" not in result["text"]


def test_without_speaker_detection_the_whole_system_track_is_speaker_2(meeting_api, tmp_path):
    api, _ = meeting_api
    fake = api._transcribe_wav_chunks

    def no_detection(chunks, **kwargs):
        result = fake(chunks, **kwargs)
        if kwargs["diarize"]:
            result.notes = [
                "Speaker detection needs Parakeet as the active model; the transcript has no speaker labels."
            ]
        return result

    api._transcribe_wav_chunks = no_detection
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert result["ok"]
    assert result["text"] == "Speaker 2: hello everyone\n\nSpeaker 1: sounds good"
    assert any("shown as Speaker 2" in n for n in result["notes"])


def test_own_voice_label_setting_is_saved_and_cleaned(meeting_api):
    api, _ = meeting_api

    assert api.get_settings()["meeting_own_voice_label"] == "Speaker 1"
    assert api.set_meeting_own_voice_label("  Alex\n Miller ")["meeting_own_voice_label"] == "Alex Miller"
    assert api.set_meeting_own_voice_label("   ")["meeting_own_voice_label"] == "Speaker 1"


def test_the_own_voice_name_is_stored_with_the_row_and_survives_in_the_hook_view(meeting_api, db, tmp_path):
    api, hook = meeting_api
    api._config.meeting.own_voice_label = "Alex"
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert json.loads(db.get_file_transcription(result["id"]).speaker_names) == {"1": "Alex"}
    assert hook.seen[0].speaker_names == {1: "Alex"}


@pytest.mark.parametrize("label", ["Speaker 2", "speaker 3", "SPEAKER   4"])
def test_a_label_that_another_speaker_gets_is_refused(meeting_api, label):
    api, _ = meeting_api
    api._config.meeting.own_voice_label = "Alex"

    resp = api.set_meeting_own_voice_label(label)

    assert "another speaker" in resp["error"]
    assert api._config.meeting.own_voice_label == "Alex"
    assert api.set_meeting_own_voice_label("Speaker 1")["meeting_own_voice_label"] == "Speaker 1"


def test_a_hand_edited_clashing_label_falls_back_to_the_default(tmp_path):
    from nexusvox.config import load_config

    path = tmp_path / "config.toml"
    path.write_text('[meeting]\nown_voice_label = "Speaker 2"\n', encoding="utf-8")

    assert load_config(path).meeting.own_voice_label == "Speaker 1"


def test_progress_does_not_fall_back_while_speakers_are_detected(meeting_api, tmp_path):
    api, _ = meeting_api
    fake = api._transcribe_wav_chunks

    def with_diarizing(chunks, **kwargs):
        kwargs["on_progress"]("transcribing", len(chunks), len(chunks))
        if kwargs["diarize"]:
            kwargs["on_progress"]("diarizing")
        return fake(chunks, **kwargs)

    api._transcribe_wav_chunks = with_diarizing
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))
    seen = []

    class Job:
        def update(self, stage, current=0, total=0):
            seen.append((stage, current))

    api.transcribe_meeting(folder, job=Job())

    currents = [c for stage, c in seen if stage in ("transcribing", "diarizing")]
    assert currents == sorted(currents)


def test_a_very_long_system_track_skips_speaker_detection(meeting_api, tmp_path, monkeypatch):
    api, _ = meeting_api
    fake = api._transcribe_wav_chunks
    asked = []

    def spy(chunks, **kwargs):
        asked.append(kwargs["diarize"])
        return fake(chunks, **kwargs)

    api._transcribe_wav_chunks = spy
    monkeypatch.setattr("nexusvox.dashboard.api.MAX_DETECTION_S", 1.0)
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert asked == [False, False]
    assert any("too long for speaker detection" in n for n in result["notes"])


def test_detected_speakers_that_cannot_be_used_are_noted(meeting_api, tmp_path):
    api, _ = meeting_api

    def partly_timed(chunks, **kwargs):
        result = TranscriptionResult()
        if kwargs["diarize"]:
            result.words = [Word("hello", 1.0, 1.3, 1), Word("hi", 2.0, 2.3, 2)]
            result.finalize(text="Speaker 1: hello\n\nSpeaker 2: hi")
            result.plain_text = "hello hi"
        else:
            result.words = [w("sounds", 3.0)]  # "good" has no timing
            result.finalize(text="sounds good")
        return result

    api._transcribe_wav_chunks = partly_timed
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert result["text"] == "Speaker 1: sounds good\n\nSpeaker 2: hello hi"
    assert any("detected speakers could not be used" in n for n in result["notes"])


def test_a_skipped_speaker_detection_says_that_everyone_is_speaker_2(meeting_api, tmp_path):
    api, _ = meeting_api
    fake = api._transcribe_wav_chunks

    def skipped(chunks, **kwargs):
        result = fake(chunks, **kwargs)
        if kwargs["diarize"]:
            result.notes = ["Speaker detection skipped: some words came back without timing."]
        return result

    api._transcribe_wav_chunks = skipped
    folder = tmp_path / "m"
    write_meeting(folder, tone(2, 3000), tone(2, 3000))

    result = api.transcribe_meeting(folder)

    assert any("everyone on it is shown as Speaker 2" in n for n in result["notes"])
