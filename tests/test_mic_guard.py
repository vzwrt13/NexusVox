"""Tests for the remember/restore logic of the mic guard, with a faked session list."""

from __future__ import annotations

import json
import threading

from nexusvox.audio.mic_guard import STATE_FILENAME, MicGuard


class FakeSession:
    def __init__(self, pid: int, muted: bool = False, *, app: str = "app"):
        self.pid = pid
        self.session_id = f"{app}|sid"
        self.instance_id = f"{app}|sid|{pid}"
        self.muted = muted
        self.set_calls: list[bool] = []

    def get_mute(self) -> bool:
        return self.muted

    def set_mute(self, muted: bool) -> None:
        self.set_calls.append(muted)
        self.muted = muted


def _guard(sessions, **kw) -> MicGuard:
    kw.setdefault("own_pids", {999})
    kw.setdefault("watchdog_s", 0)
    return MicGuard(lambda: list(sessions), **kw)


def test_hold_mutes_foreign_unmuted_sessions_only():
    discord = FakeSession(1, app="discord")
    already_muted = FakeSession(2, muted=True, app="teams")
    own = FakeSession(999, app="nexusvox")
    system = FakeSession(0, app="system")
    g = _guard([discord, already_muted, own, system])

    muted = g.hold()

    assert [e.pid for e in muted] == [1]
    assert discord.muted is True
    assert already_muted.set_calls == []
    assert own.set_calls == []
    assert system.set_calls == []
    assert g.active


def test_release_restores_only_what_hold_muted():
    discord = FakeSession(1, app="discord")
    purposely_muted = FakeSession(2, muted=True, app="teams")
    g = _guard([discord, purposely_muted])

    g.hold()
    assert g.release() == 1

    assert discord.muted is False
    assert purposely_muted.muted is True  # never touched
    assert not g.active


def test_release_is_idempotent_and_hold_does_not_stack():
    s = FakeSession(1)
    g = _guard([s])

    g.hold()
    g.hold()  # second hold while active is a no-op
    assert s.set_calls == [True]
    assert g.release() == 1
    assert g.release() == 0
    assert s.set_calls == [True, False]


def test_release_matches_by_instance_id_not_object_identity():
    # Sessions are re-enumerated on release; a fresh wrapper for the same session must match.
    first = FakeSession(1)
    second = FakeSession(1)
    current = [first]
    g = _guard(current)

    g.hold()
    current[0] = second
    assert g.release() == 1
    assert second.muted is False


def test_release_survives_vanished_session():
    s = FakeSession(1)
    current = [s]
    g = _guard(current)

    g.hold()
    current.clear()
    assert g.release() == 0
    assert not g.active


def test_enumeration_failure_mutes_nothing():
    def boom():
        raise RuntimeError("no default capture device")

    g = MicGuard(boom, own_pids=set(), watchdog_s=0)
    assert g.hold() == []
    assert not g.active


def test_release_keeps_state_when_enumeration_fails(tmp_path):
    s = FakeSession(1, app="discord")
    path = tmp_path / STATE_FILENAME
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("default capture device changed")
        return [s]

    g = MicGuard(flaky, own_pids=set(), watchdog_s=0, state_path=path)
    g.hold()

    # The failed release touches nothing and forgets nothing, so the next one can retry.
    assert g.release() == 0
    assert g.active
    assert s.muted is True
    assert path.exists()

    assert g.release() == 1
    assert not g.active
    assert s.muted is False
    assert not path.exists()


def test_set_mute_failure_is_not_remembered():
    good = FakeSession(1)
    bad = FakeSession(2)

    def fail(_muted):
        raise OSError("session gone")

    bad.set_mute = fail
    g = _guard([good, bad])

    assert [e.pid for e in g.hold()] == [1]
    assert g.release() == 1


def test_state_file_written_during_hold_and_removed_on_release(tmp_path):
    s = FakeSession(1, app="discord")
    path = tmp_path / STATE_FILENAME
    g = _guard([s], state_path=path)

    g.hold()
    data = json.loads(path.read_text())
    assert data["entries"][0]["instance_id"] == s.instance_id

    g.release()
    assert not path.exists()


def test_restore_leftovers_unmutes_by_instance_or_stable_id(tmp_path):
    path = tmp_path / STATE_FILENAME
    path.write_text(
        json.dumps(
            {
                "ts": 0,
                "entries": [
                    {"pid": 1, "instance_id": "discord|sid|1", "session_id": "discord|sid"},
                ],
            }
        )
    )
    restarted_discord = FakeSession(42, muted=True, app="discord")  # new PID, same stable id
    unrelated = FakeSession(7, muted=True, app="obs")
    g = _guard([restarted_discord, unrelated], state_path=path)

    assert g.restore_leftovers() == 1
    assert restarted_discord.muted is False
    assert unrelated.muted is True
    assert not path.exists()


def test_restore_leftovers_without_state_file_is_noop(tmp_path):
    s = FakeSession(1, muted=True)
    g = _guard([s], state_path=tmp_path / STATE_FILENAME)
    assert g.restore_leftovers() == 0
    assert s.muted is True


def test_watchdog_releases_when_keys_are_up():
    s = FakeSession(1)
    g = _guard([s], watchdog_s=0.05, still_held=lambda: False)

    g.hold()
    for _ in range(100):
        if not g.active:
            break
        threading.Event().wait(0.02)
    assert not g.active
    assert s.muted is False


def test_watchdog_rearms_while_keys_are_held():
    s = FakeSession(1)
    g = _guard([s], watchdog_s=0.02, still_held=lambda: True)

    g.hold()
    threading.Event().wait(0.1)  # several watchdog periods
    assert s.muted is True
    assert g.active
    g.release()
    assert s.muted is False


def test_async_hold_then_release_keeps_order_on_quick_tap():
    s = FakeSession(1)
    g = _guard([s])

    g.hold_async()
    g.release_async()
    g._worker.shutdown(wait=True)

    assert s.set_calls == [True, False]
    assert not g.active


def test_release_restores_a_session_the_app_recreated_during_the_hold():
    # Discord rejoining a call creates a new session (new instance id); Windows carries the mute over to it
    # under the stable session identifier, so the release must find it by that identifier.
    discord = FakeSession(1, app="discord")
    current = [discord]
    g = _guard(current)

    g.hold()
    rejoined = FakeSession(1, muted=True, app="discord")
    rejoined.instance_id = "discord|sid|rejoined"
    current[0] = rejoined

    assert g.release() == 1
    assert rejoined.muted is False
    assert not g.active


def test_restoring_by_session_id_leaves_a_sibling_the_user_muted_alone():
    discord = FakeSession(1, app="discord")
    user_muted = FakeSession(5, muted=True, app="discord")  # same app, same stable id, muted on purpose
    g = _guard([discord, user_muted])

    g.hold()
    assert g.release() == 1
    assert discord.muted is False
    assert user_muted.muted is True
    assert user_muted.set_calls == []


def test_hold_mutes_nothing_when_the_state_file_cannot_be_written(tmp_path, caplog):
    s = FakeSession(1, app="discord")
    g = _guard([s], state_path=tmp_path / "missing-dir" / STATE_FILENAME)

    assert g.hold() == []
    assert s.set_calls == []
    assert s.muted is False
    assert not g.active


def test_a_session_the_user_starts_and_mutes_during_the_hold_stays_muted():
    discord = FakeSession(1, app="discord")
    current = [discord]
    g = _guard(current)

    g.hold()
    second = FakeSession(5, muted=True, app="discord")  # started during the hold, muted on purpose
    current.append(second)

    assert g.release() == 1
    assert discord.muted is False
    assert second.muted is True  # the muted session is still there: nothing to stand in for
    assert second.set_calls == []


def test_one_stand_in_per_vanished_session_and_the_rest_is_reported_missing(caplog):
    a1 = FakeSession(1, app="discord")
    a2 = FakeSession(2, app="discord")
    current = [a1, a2]
    g = _guard(current)

    g.hold()
    current.remove(a1)
    current.remove(a2)
    stand_in = FakeSession(3, muted=True, app="discord")
    current.append(stand_in)

    with caplog.at_level("WARNING", logger="nexusvox.mic_guard"):
        assert g.release() == 1
    assert stand_in.muted is False
    assert any("1 muted session(s) vanished" in r.getMessage() for r in caplog.records)


def test_nexusvox_own_session_is_never_a_stand_in():
    other = FakeSession(1, app="python")  # another app on the same interpreter shares the stable id
    current = [other]
    g = _guard(current, own_pids={999})

    g.hold()
    current.clear()
    own = FakeSession(999, muted=True, app="python")  # NexusVox's own session, muted by the user
    current.append(own)

    assert g.release() == 0
    assert own.muted is True
    assert own.set_calls == []


def _killed_during_hold(path, sessions):
    """A hold whose process dies: the state file stays, nothing is released."""
    _guard(sessions, state_path=path).hold()


def test_restore_after_a_crash_leaves_a_sibling_the_user_muted_alone(tmp_path):
    path = tmp_path / STATE_FILENAME
    muted_by_guard = FakeSession(1, app="discord")
    user_muted = FakeSession(5, muted=True, app="discord")  # same stable id, muted by the user before the hold
    _killed_during_hold(path, [muted_by_guard, user_muted])

    assert _guard([muted_by_guard, user_muted], state_path=path).restore_leftovers() == 1
    assert muted_by_guard.muted is False
    assert user_muted.muted is True
    assert user_muted.set_calls == []


def test_restore_after_a_crash_unmutes_a_restarted_session_but_not_the_user_muted_sibling(tmp_path):
    path = tmp_path / STATE_FILENAME
    muted_by_guard = FakeSession(1, app="discord")
    user_muted = FakeSession(5, muted=True, app="discord")
    _killed_during_hold(path, [muted_by_guard, user_muted])

    restarted = FakeSession(42, muted=True, app="discord")  # the muted one restarted and inherited the mute
    assert _guard([restarted, user_muted], state_path=path).restore_leftovers() == 1
    assert restarted.muted is False
    assert user_muted.muted is True


def test_restored_count_is_not_inflated_by_extra_same_app_sessions():
    a1 = FakeSession(1, app="discord")
    a2 = FakeSession(2, app="discord")
    current = [a1, a2]
    g = _guard(current)

    g.hold()
    current.remove(a2)
    new_one, new_two = FakeSession(3, muted=True, app="discord"), FakeSession(4, muted=True, app="discord")
    current += [new_one, new_two]

    assert g.release() == 2  # a1 and one stand-in for a2
    assert a1.muted is False
    assert [new_one.muted, new_two.muted].count(False) == 1
