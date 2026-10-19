"""In-memory registry of file transcription jobs, so the Upload tab can show how far one is."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

# Progress bar position (0..1) at the start and end of the chunk loop; conversion comes before, saving after.
_CHUNKS_FROM = 0.1
_CHUNKS_TO = 0.9
_KEEP_FINISHED = 10


class FileJob:
    """One upload being transcribed. Updated from the worker thread, read from request threads."""

    def __init__(self, filename: str) -> None:
        self.id = uuid.uuid4().hex
        self.filename = filename
        self.state = "running"  # running | done | error
        self.stage = "converting"
        self.current = 0
        self.total = 0
        self.result: dict | None = None
        self.error = ""
        self.acknowledged = False  # a client has been told how it ended
        self.started = time.monotonic()
        self._lock = threading.Lock()

    def update(self, stage: str, current: int = 0, total: int = 0) -> None:
        """Move to a stage: `converting`, `transcribing` (chunk `current` of `total`), `diarizing` or `saving`."""
        with self._lock:
            if self.state == "running":
                self.stage, self.current, self.total = stage, current, total

    def finish(self, result: dict) -> None:
        """End the job with the dict `upload_and_transcribe` returned: `ok` is done, anything else an error."""
        with self._lock:
            if result.get("ok"):
                self.state, self.result = "done", result
            else:
                self.state, self.error = "error", str(result.get("error") or "Transcription failed")

    def snapshot(self) -> dict:
        with self._lock:
            if self.state == "done":
                message, percent = "Done", 1.0
            elif self.state == "error":
                message, percent = self.error, 0.0
            else:
                message, percent = _describe(self.stage, self.current, self.total)
            return {
                "job_id": self.id,
                "filename": self.filename,
                "state": self.state,
                "stage": self.stage,
                "current": self.current,
                "total": self.total,
                "message": message,
                "percent": round(percent, 3),
                "result": self.result,
                "error": self.error,
            }


def _describe(stage: str, current: int, total: int) -> tuple[str, float]:
    """The stage as text for the progress bar and how far along the bar it is."""
    if stage == "transcribing" and total:
        done = (current - 1) / total  # the chunk in progress is not finished yet
        return f"Transcribing chunk {current} of {total}", _CHUNKS_FROM + (_CHUNKS_TO - _CHUNKS_FROM) * done
    if stage == "diarizing":
        return "Detecting speakers", _CHUNKS_TO
    if stage == "saving":
        return "Saving", 0.95
    return "Converting audio", 0.0


class FileJobs:
    """Starts file jobs on a thread and remembers the latest ones. One job runs at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, FileJob] = {}

    def start(self, filename: str, work: Callable[[FileJob], dict]) -> FileJob | None:
        """Run `work(job)` on a thread; None when another job is still running.

        `work` returns the result dict for `FileJob.finish`; an exception ends the job as an error.
        """
        with self._lock:
            if any(j.state == "running" for j in self._jobs.values()):
                return None
            job = FileJob(filename)
            self._jobs[job.id] = job
            for old in [j for j in self._jobs.values() if j.state != "running"][:-_KEEP_FINISHED]:
                del self._jobs[old.id]
        threading.Thread(target=self._run, args=(job, work), name=f"file-job-{job.id[:8]}", daemon=True).start()
        return job

    @staticmethod
    def _run(job: FileJob, work: Callable[[FileJob], dict]) -> None:
        try:
            job.finish(work(job))
        except Exception as exc:
            job.finish({"ok": False, "error": f"Transcription failed: {exc}"})

    def get(self, job_id: str) -> dict | None:
        """The job's status. A finished job counts as seen once a client has read it."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        snap = job.snapshot()
        if snap["state"] != "running":
            job.acknowledged = True
        return snap

    def current(self) -> dict | None:
        """The latest job if it is running or no client has read how it ended yet, else None.

        A page opened (or reloaded) mid-job picks the job up again through this, and learns how a job
        that ended while it was away turned out.
        """
        with self._lock:
            latest = next(reversed(self._jobs.values()), None)
        if latest is None or (latest.state != "running" and latest.acknowledged):
            return None
        return self.get(latest.id)
