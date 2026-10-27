"""The `display_text` hook and `UploadAction.available`."""

import time

import pytest

import nexusvox.extensions as ext
from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.extensions import Extension, ExtensionRegistry, FileTranscript, UploadAction
from nexusvox.transcriber import Word


class Namer(Extension):
    name = "namer"
    api_version = 1

    def display_text(self, transcript, text):
        return text.replace("Speaker 1", "Alice").replace("Speaker 2", "Bob")


def make_registry(*extensions):
    registry = ExtensionRegistry()
    registry.add_builtin_exports()
    for extension in extensions:
        assert registry.add(extension)
    return registry


def transcript(**kwargs):
    return FileTranscript(id=1, text="Speaker 1: hi", language="en", duration_ms=1, original_filename="a.wav", **kwargs)


def client_for(db, session_factory, registry):
    app = _create_app(DashboardAPI(session_factory, Config(), db=db, extensions=registry))
    app.config["TESTING"] = True
    return app.test_client()


def spoken_upload(db):
    words = [Word("hello", 0.0, 0.4, 1), Word("there", 0.4, 0.8, 1), Word("hi", 1.0, 1.4, 2)]
    return db.save_file_transcription(
        "Speaker 1: hello there\n\nSpeaker 2: hi", "en", 1500, "Call.wav", words=words, cleaned_text="Speaker 1: Hello."
    )


# ---- display_text ----------------------------------------------------------


def test_without_an_extension_the_texts_are_unchanged():
    assert ExtensionRegistry().display_texts(transcript(), ["a", "b"]) == ["a", "b"]


def test_extensions_change_the_text_in_order():
    class Shout(Extension):
        name = "shout"
        api_version = 1

        def display_text(self, transcript, text):
            return text.upper()

    registry = make_registry(Namer(), Shout())

    assert registry.display_texts(transcript(), ["Speaker 1: hi"]) == ["ALICE: HI"]


def test_none_a_failure_a_non_string_and_an_empty_answer_keep_the_text():
    answers = iter([None, RuntimeError("boom"), 42, ""])

    class Odd(Extension):
        name = "odd"
        api_version = 1

        def display_text(self, transcript, text):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

    registry = make_registry(Odd())

    assert registry.display_texts(transcript(), ["a", "b", "c", "d"]) == ["a", "b", "c", "d"]


def test_a_slow_hook_is_dropped_and_not_called_again(monkeypatch):
    monkeypatch.setattr(ext, "HOOK_TIMEOUT_S", 0.05)
    calls = []

    class Slow(Extension):
        name = "slow"
        api_version = 1

        def display_text(self, transcript, text):
            calls.append(text)
            time.sleep(0.3)
            return "late"

    registry = make_registry(Slow())

    assert registry.display_texts(transcript(), ["a", "b"]) == ["a", "b"]
    assert calls == ["a"]


def test_the_upload_list_shows_the_changed_text(db, session_factory):
    spoken_upload(db)
    row = client_for(db, session_factory, make_registry(Namer())).get("/api/file-transcriptions").get_json()[0]

    assert row["text"] == "Alice: hello there\n\nBob: hi"
    assert row["cleaned_text"] == "Alice: Hello."


def test_the_upload_list_is_raw_without_an_extension_and_after_a_failure(db, session_factory):
    spoken_upload(db)

    class Broken(Extension):
        name = "broken"
        api_version = 1

        def display_text(self, transcript, text):
            raise ValueError("no")

    for registry in (make_registry(), make_registry(Broken())):
        row = client_for(db, session_factory, registry).get("/api/file-transcriptions").get_json()[0]
        assert row["text"].startswith("Speaker 1: hello there")


def test_the_builtin_exports_use_the_changed_text(db, session_factory):
    record = spoken_upload(db)
    client = client_for(db, session_factory, make_registry(Namer()))

    def export(target):
        return client.get(f"/api/file-transcriptions/{record.id}/export/{target}").data.decode()

    assert export("txt") == "Alice: Hello.\n"
    assert "Alice: hello there" in export("srt") and "Bob: hi" in export("srt")
    assert "Speaker" not in export("vtt") and "Alice: hello there" in export("vtt")


def test_the_builtin_exports_are_raw_without_an_extension_and_after_a_failure(db, session_factory):
    record = spoken_upload(db)

    class Broken(Extension):
        name = "broken"
        api_version = 1

        def display_text(self, transcript, text):
            raise ValueError("no")

    for registry in (make_registry(), make_registry(Broken())):
        client = client_for(db, session_factory, registry)
        assert client.get(f"/api/file-transcriptions/{record.id}/export/txt").data == b"Speaker 1: Hello.\n"
        assert b"Speaker 1: hello there" in client.get(f"/api/file-transcriptions/{record.id}/export/srt").data


def test_an_extension_without_the_hook_loads_and_changes_nothing():
    class Old(Extension):
        name = "old"
        api_version = 1

    registry = make_registry(Old())

    assert registry.display_texts(transcript(), ["x"]) == ["x"]


# ---- UploadAction.available ------------------------------------------------


def action_registry(available):
    class Pro(Extension):
        name = "pro"
        api_version = 1

        def upload_actions(self):
            return [UploadAction("speakers", "Speakers", endpoint="/api/ext/x/{id}", available=available)]

    return make_registry(Pro())


def test_unavailable_actions_carry_their_reason():
    assert action_registry(lambda tid: "Licence needed").unavailable_actions(1) == {"speakers": "Licence needed"}
    assert action_registry(lambda tid: False).unavailable_actions(1) == {"speakers": "Not available"}
    assert action_registry(lambda tid: True).unavailable_actions(1) == {}
    assert action_registry(lambda tid: None).unavailable_actions(1) == {}
    assert action_registry(None).unavailable_actions(1) == {}


def test_a_failing_action_check_leaves_the_action_on():
    def boom(tid):
        raise RuntimeError("db gone")

    assert action_registry(boom).unavailable_actions(1) == {}


def test_an_action_with_an_uncallable_check_is_dropped():
    assert action_registry("yes").upload_actions() == []


def test_the_upload_list_marks_unavailable_actions(db, session_factory):
    first = db.save_file_transcription("one", "en", 1000, "a.wav")
    second = db.save_file_transcription("two", "en", 1000, "b.wav")
    registry = action_registry(lambda tid: "Licence needed" if tid == first.id else True)

    rows = {r["id"]: r for r in client_for(db, session_factory, registry).get("/api/file-transcriptions").get_json()}

    assert rows[first.id]["actions_unavailable"] == {"speakers": "Licence needed"}
    assert rows[second.id]["actions_unavailable"] == {}


@pytest.mark.parametrize("registry", [ExtensionRegistry()])
def test_an_empty_registry_greys_nothing_out(registry):
    assert registry.unavailable_actions(1) == {}


# ---- Review fixes: budgets, batching, cue safety ---------------------------


def test_each_extension_gets_all_texts_in_one_thread():
    threads = set()

    class Spy(Extension):
        name = "spy"
        api_version = 1

        def display_text(self, transcript, text):
            import threading

            threads.add(threading.get_ident())
            return text

    make_registry(Spy()).display_texts(transcript(), [f"t{i}" for i in range(200)])

    assert len(threads) == 1


def test_a_hook_that_runs_out_of_time_leaves_every_text_raw(monkeypatch):
    monkeypatch.setattr(ext, "HOOK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(ext, "DISPLAY_TEXT_S", 0.0)

    class Half(Extension):
        name = "half"
        api_version = 1

        def display_text(self, transcript, text):
            time.sleep(0.03)
            return text.upper()

    texts = [f"t{i}" for i in range(10)]

    assert make_registry(Half()).display_texts(transcript(), texts) == texts


def test_one_failing_text_does_not_spoil_the_others():
    class Picky(Extension):
        name = "picky"
        api_version = 1

        def display_text(self, transcript, text):
            if text == "bad":
                raise ValueError("no")
            return text.upper()

    assert make_registry(Picky()).display_texts(transcript(), ["a", "bad", "c"]) == ["A", "bad", "C"]


def test_a_slow_extension_costs_the_list_one_budget_not_one_per_row(db, session_factory, monkeypatch):
    monkeypatch.setattr(ext, "HOOK_TIMEOUT_S", 0.2)
    monkeypatch.setattr("nexusvox.dashboard.api.HOOK_TIMEOUT_S", 0.2)

    class Slow(Extension):
        name = "slow"
        api_version = 1

        def display_text(self, transcript, text):
            time.sleep(0.1)
            return "x"

    for i in range(10):
        db.save_file_transcription("t", "en", 1, f"{i}.wav", cleaned_text="c")
    started = time.monotonic()
    rows = client_for(db, session_factory, make_registry(Slow())).get("/api/file-transcriptions").get_json()

    assert time.monotonic() - started < 1.5
    assert len(rows) == 10


def test_a_row_gets_text_and_cleaned_text_in_one_call(db, session_factory):
    calls = []

    class Count(Extension):
        name = "count"
        api_version = 1

        def display_text(self, transcript, text):
            calls.append(text)
            return text

    db.save_file_transcription("raw", "en", 1, "a.wav", cleaned_text="clean")
    client_for(db, session_factory, make_registry(Count())).get("/api/file-transcriptions")

    assert calls == ["raw", "clean"]


def test_changed_cues_are_rewrapped_and_a_cue_that_would_break_the_file_stays(db, session_factory):
    record = spoken_upload(db)

    class Longname(Extension):
        name = "longname"
        api_version = 1

        def display_text(self, transcript, text):
            if text.startswith("Speaker 1"):
                return text.replace("Speaker 1", "Dr. Alexandra Montgomery-Smith the Third")
            return text.replace("Speaker 2", "A\n\nB --> C")

    srt = client_for(db, session_factory, make_registry(Longname())).get(
        f"/api/file-transcriptions/{record.id}/export/srt"
    )
    body = srt.data.decode()

    assert all(len(line) <= 42 for line in body.splitlines())
    assert "-->" not in body.replace(" --> ", "")  # only the timing lines carry an arrow
    assert "Speaker 2: hi" in body  # the unsafe answer was dropped
    assert "\n\n\n" not in body


def test_a_hanging_availability_check_does_not_hang_the_list(db, session_factory, monkeypatch):
    monkeypatch.setattr(ext, "HOOK_TIMEOUT_S", 0.1)
    monkeypatch.setattr("nexusvox.dashboard.api.HOOK_TIMEOUT_S", 0.1)
    release = []

    def hang(tid):
        while not release:
            time.sleep(0.01)
        return "never"

    for i in range(5):
        db.save_file_transcription("t", "en", 1, f"{i}.wav")
    started = time.monotonic()
    try:
        rows = client_for(db, session_factory, action_registry(hang)).get("/api/file-transcriptions").get_json()
    finally:
        release.append(True)

    assert time.monotonic() - started < 1.5
    assert all(row["actions_unavailable"] == {} for row in rows)  # offered: the click reports the problem
