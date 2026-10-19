"""Tests for the in-memory file job registry."""

from __future__ import annotations

import threading

from nexusvox.dashboard.file_jobs import FileJob, FileJobs


def _join_workers() -> None:
    for t in threading.enumerate():
        if t.name.startswith("file-job-"):
            t.join(5)


def test_percent_grows_with_the_stages():
    job = FileJob("a.wav")
    positions = []
    for stage, current, total in [
        ("converting", 0, 0),
        ("transcribing", 1, 4),
        ("transcribing", 3, 4),
        ("diarizing", 0, 0),
        ("saving", 0, 0),
    ]:
        job.update(stage, current, total)
        positions.append(job.snapshot()["percent"])

    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert job.snapshot()["message"] == "Saving"


def test_finish_maps_the_result_to_done_or_error():
    done, failed = FileJob("a.wav"), FileJob("b.wav")
    done.finish({"ok": True, "text": "hi"})
    failed.finish({"ok": False, "error": "nope"})

    assert (done.snapshot()["state"], done.snapshot()["result"]) == ("done", {"ok": True, "text": "hi"})
    assert (failed.snapshot()["state"], failed.snapshot()["error"]) == ("error", "nope")


def test_a_finished_job_ignores_late_progress():
    job = FileJob("a.wav")
    job.finish({"ok": True})
    job.update("transcribing", 1, 2)

    assert job.snapshot()["stage"] == "converting" and job.snapshot()["percent"] == 1.0


def test_only_one_job_runs_at_a_time():
    jobs = FileJobs()
    release = threading.Event()

    first = jobs.start("a.wav", lambda job: (release.wait(5), {"ok": True})[1])
    second = jobs.start("b.wav", lambda job: {"ok": True})
    release.set()
    _join_workers()

    assert first is not None and second is None
    assert jobs.start("c.wav", lambda job: {"ok": True}) is not None  # free again once the first ended


def test_an_exception_in_the_work_becomes_an_error():
    jobs = FileJobs()

    def work(job):
        raise ValueError("bad")

    job = jobs.start("a.wav", work)
    _join_workers()

    snap = jobs.get(job.id)
    assert snap["state"] == "error" and snap["error"] == "Transcription failed: bad"


def test_current_returns_a_finished_job_until_it_was_read():
    jobs = FileJobs()
    job = jobs.start("a.wav", lambda job: {"ok": True})
    _join_workers()

    assert jobs.current()["job_id"] == job.id  # reading it through `current` counts as reading it
    assert jobs.current() is None


def test_finished_jobs_are_pruned():
    jobs = FileJobs()
    for i in range(15):
        jobs.start(f"{i}.wav", lambda job: {"ok": True})
        _join_workers()

    assert len(jobs._jobs) == 11  # the last ten that ended and the one started after pruning
