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
