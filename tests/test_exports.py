"""Built-in TXT / SRT / VTT export of stored uploads."""

import pytest

from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.exports import MAX_CUE_S, MAX_LINE_CHARS, MAX_LINES, build_cues, to_srt, to_txt, to_vtt
from nexusvox.extensions import ExtensionRegistry, FileTranscript
from nexusvox.transcriber import Word


def transcript(words, text="", kind="upload", **kwargs):
    return FileTranscript(
        id=1,
        text=text,
        language="en",
        duration_ms=1000,
        original_filename="a.wav",
        words=tuple(words),
        kind=kind,
        **kwargs,
    )


def spoken(text, start=0.0, step=0.4, speaker=None):
    """One Word per token, `step` seconds apart."""
    return [Word(t, start + i * step, start + i * step + step - 0.05, speaker) for i, t in enumerate(text.split())]


def test_srt_has_numbered_cues_with_comma_timestamps():
    srt = to_srt(transcript(spoken("Hello there. How are you today?", step=0.5)))

    assert srt.startswith("1\n00:00:00,000 --> ")
    assert srt.endswith("\n")
    long_srt = to_srt(transcript(spoken("This is the first sentence. And here comes the second one", step=0.3)))
    assert "\n\n2\n00:00:01,500 --> 00:00:03,250\nAnd here" in long_srt


def test_vtt_has_header_and_dot_timestamps_and_escapes():
    vtt = to_vtt(transcript([Word("a<b", 3661.5, 3662.0), Word("&c", 3662.0, 3662.5)]))

    assert vtt.startswith("WEBVTT\n\n")
    assert "01:01:01.500 --> 01:01:02.500" in vtt
    assert "a&lt;b &amp;c" in vtt


def test_a_cue_never_exceeds_seven_seconds_or_two_lines():
    words = spoken("word " * 60, step=0.5)
    cues = build_cues(transcript(words))

    assert len(cues) > 1
    for start, end, text in cues:
        assert end - start <= MAX_CUE_S
        lines = text.split("\n")
        assert len(lines) <= MAX_LINES
        assert all(len(line) <= MAX_LINE_CHARS for line in lines)


def test_a_pause_splits_the_cue():
    words = spoken("one two", 0.0) + spoken("three four", 5.0)

    cues = build_cues(transcript(words))

    assert [c[2] for c in cues] == ["one two", "three four"]


def test_a_long_sentence_end_splits_after_the_sentence():
    cues = build_cues(transcript(spoken("This is the first sentence. And here comes the second one", step=0.3)))

    assert cues[0][2].endswith("sentence.")
    assert cues[1][2].startswith("And")


def test_speaker_prefix_marks_each_turn_and_splits_cues():
    words = (
        spoken("Hallo zusammen", 0.0, speaker=1)
        + spoken("Guten Tag", 1.0, speaker=2)
        + spoken("Danke dir", 2.0, speaker=1)
    )

    cues = build_cues(transcript(words))

    assert [c[2] for c in cues] == ["Speaker 1: Hallo zusammen", "Speaker 2: Guten Tag", "Speaker 1: Danke dir"]


def test_a_speaker_turn_split_over_two_cues_is_labelled_once():
    words = spoken("word " * 40, step=0.5, speaker=1)

    texts = [c[2] for c in build_cues(transcript(words))]

    assert texts[0].startswith("Speaker 1: ")
    assert all(not t.startswith("Speaker") for t in texts[1:])


def test_meeting_speakers_are_numbered_and_the_own_voice_keeps_its_stored_name():
    words = spoken("hi", 0.0, speaker=1) + spoken("hello", 1.0, speaker=2)

    cues = build_cues(transcript(words, text="Alex: hi\n\nSpeaker 2: hello", kind="meeting"))

    assert [c[2] for c in cues] == ["Alex: hi", "Speaker 2: hello"]


def test_meeting_without_a_readable_text_falls_back_to_numbers():
    words = spoken("hi", 0.0, speaker=1) + spoken("hello", 1.0, speaker=2)

    cues = build_cues(transcript(words, text="edited", kind="meeting"))

    assert [c[2] for c in cues] == ["Speaker 1: hi", "Speaker 2: hello"]


def test_cues_do_not_overlap():
    words = [Word("a", 0.0, 2.0), Word("b", 1.0, 1.5)]

    cues = build_cues(transcript(words))

    assert all(cues[i][1] <= cues[i + 1][0] for i in range(len(cues) - 1))


def test_subtitles_need_word_timestamps():
    with pytest.raises(ValueError, match="word timestamps"):
        to_srt(transcript([], text="hello"))
    with pytest.raises(ValueError, match="word timestamps"):
        to_vtt(transcript([], text="hello"))


def test_txt_prefers_the_cleaned_text():
    assert to_txt(transcript([], text="um hello", cleaned_text="Hello.")) == "Hello.\n"
    assert to_txt(transcript([], text="Speaker 1: hi\n\nSpeaker 2: yo")) == "Speaker 1: hi\n\nSpeaker 2: yo\n"


def test_german_text_survives_the_export():
    srt = to_srt(transcript(spoken("Schöne Grüße aus Köln", step=0.4)))

    assert "Schöne Grüße aus Köln" in srt


@pytest.fixture
def client(db, session_factory):
    registry = ExtensionRegistry()
    registry.add_builtin_exports()
    api = DashboardAPI(session_factory, Config(), db=db, extensions=registry)
    app = _create_app(api)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_the_dashboard_offers_txt_srt_and_vtt(client):
    ids = [e["id"] for e in client.get("/api/extensions").get_json()["exports"]]

    assert ids == ["txt", "srt", "vtt"]


def test_downloading_an_upload_as_srt_and_txt(client, db):
    record = db.save_file_transcription(
        "hello world", "en", 1000, "Call.wav", words=[Word("hello", 0.0, 0.4), Word("world", 0.4, 0.9)]
    )

    srt = client.get(f"/api/file-transcriptions/{record.id}/export/srt")
    txt = client.get(f"/api/file-transcriptions/{record.id}/export/txt")

    assert srt.status_code == 200
    assert srt.data.decode() == "1\n00:00:00,000 --> 00:00:00,900\nhello world\n"
    assert 'filename="Call.srt"' in srt.headers["Content-Disposition"]
    assert txt.data == b"hello world\n"


def test_srt_without_word_timing_answers_with_a_message(client, db):
    record = db.save_file_transcription("hello", "en", 1000, "Call.wav")

    resp = client.get(f"/api/file-transcriptions/{record.id}/export/srt")

    assert resp.status_code == 500
    assert "word timestamps" in resp.get_json()["error"]


def test_a_gap_in_speaker_labels_does_not_split_the_cue_or_repeat_the_label():
    words = spoken("one two", 0.0, speaker=1) + spoken("three", 0.8, speaker=None) + spoken("four five", 1.2, speaker=1)

    cues = build_cues(transcript(words))

    assert [c[2] for c in cues] == ["Speaker 1: one two three four five"]


def test_a_continuation_cue_may_use_the_whole_two_lines():
    words = spoken("abcdefghi " * 20, step=0.4, speaker=1)  # 10-char tokens: 4 per line

    cues = build_cues(transcript(words))

    assert cues[0][2].startswith("Speaker 1: ")
    assert max(len(line) for _, _, t in cues for line in t.split("\n")) <= MAX_LINE_CHARS
    assert len(cues[1][2].replace("\n", " ")) > 73  # not capped by room for a label it does not print


def test_wrapping_keeps_every_line_within_the_limit_without_a_balanced_split():
    tokens = ["a" * 40, "bb", "c" * 40]
    cues = build_cues(transcript([Word(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(tokens)]))

    for _, _, text in cues:
        assert len(text.split("\n")) <= MAX_LINES
        assert all(len(line) <= MAX_LINE_CHARS for line in text.split("\n"))


def test_a_word_longer_than_a_line_is_cut():
    cues = build_cues(transcript([Word("x" * 60, 0.0, 1.0)]))

    assert all(len(line) <= MAX_LINE_CHARS for line in cues[0][2].split("\n"))


def test_overlapping_speech_keeps_both_speakers_and_no_empty_cue():
    words = [
        Word("yeah", 0.0, 0.5, 1),
        Word("so", 0.0, 0.4, 2),
        Word("that", 0.4, 0.8, 2),
        Word("works", 0.8, 1.2, 2),
    ]

    cues = build_cues(transcript(words, kind="meeting"))

    assert sorted(c[2] for c in cues) == ["Speaker 1: yeah", "Speaker 2: so that works"]
    assert all(end > start for start, end, _ in cues)


def test_crosstalk_does_not_shatter_a_speakers_sentence():
    words = [Word(t, i * 0.4, i * 0.4 + 0.3, 2) for i, t in enumerate("we should ship it on friday".split())]
    words.insert(2, Word("mm", 0.8, 0.9, 1))
    words.sort(key=lambda w: w.start)

    texts = [c[2] for c in build_cues(transcript(words, kind="meeting"))]

    assert "Speaker 2: we should ship it on friday" in texts


def test_the_error_names_the_kind_of_item():
    with pytest.raises(ValueError, match="this meeting"):
        to_srt(transcript([], kind="meeting"))


def test_a_failing_builtin_registration_does_not_break_loading(monkeypatch):
    monkeypatch.setattr("nexusvox.exports.builtin_export_targets", lambda: 1 / 0)
    registry = ExtensionRegistry()

    registry.add_builtin_exports()  # logs, does not raise

    assert registry.export_targets() == []


def test_srt_and_vtt_are_greyed_out_for_an_upload_without_word_timing(client, db):
    plain = db.save_file_transcription("hello", "en", 1000, "a.wav")
    timed = db.save_file_transcription("hello", "en", 1000, "b.wav", words=[Word("hello", 0.0, 0.4)])

    rows = {r["id"]: r for r in client.get("/api/file-transcriptions").get_json()}

    assert set(rows[plain.id]["exports_unavailable"]) == {"srt", "vtt"}
    assert rows[timed.id]["exports_unavailable"] == {}
