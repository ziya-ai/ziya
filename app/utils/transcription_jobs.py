"""
Background jobs for local voice transcription.

A long recording (a 30-minute meeting) takes minutes to transcribe on CPU,
far longer than one HTTP request should stay open.  The upload route hands
the received file to a job here and returns at once; the browser polls the
job for progress and the finished text, and can cancel it.

The manager knows nothing about Whisper.  The route supplies a runner
``runner(path, on_progress, cancel) -> dict`` that reports progress with
``on_progress(fraction, text_so_far)``, checks the ``cancel`` event between
segments, and raises ``TranscriptionCancelled`` when it is set.  The first
``on_progress`` call moves a job from ``queued`` to ``running``: until then
it is waiting for the model to load or for an earlier job to release it.

Jobs live in memory only.  The manager owns the uploaded file from
``submit`` onward and deletes it when the job ends, whatever the outcome.
Finished jobs are kept for ``RETENTION_SECONDS`` so a poll that lands after
completion still sees the result.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.utils.logging_utils import logger

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"
TERMINAL_STATES = frozenset({DONE, ERROR, CANCELLED})

RETENTION_SECONDS = 3600
# Unfinished jobs allowed at once.  Each holds an upload of up to the
# transcription ceiling on disk, and the model runs them one at a time.
MAX_ACTIVE_JOBS = 4

ProgressCallback = Callable[[float, str], None]
Runner = Callable[[Path, ProgressCallback, threading.Event], dict]


class TranscriptionCancelled(Exception):
    """Raised by a runner that noticed its cancel event."""


class TooManyJobs(Exception):
    """Raised by submit() when MAX_ACTIVE_JOBS jobs are unfinished."""


@dataclass
class TranscriptionJob:
    job_id: str
    filename: str
    path: Path
    state: str = QUEUED
    progress: float = 0.0
    text: str = ""
    language: Optional[str] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "state": self.state,
            "progress": round(self.progress, 4),
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "error": self.error,
            "cancel_requested": self.cancel_event.is_set(),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class TranscriptionJobManager:
    """In-memory registry of transcription jobs, one worker thread each."""

    def __init__(self) -> None:
        self._jobs: dict[str, TranscriptionJob] = {}
        self._lock = threading.Lock()

    def submit(self, path: Path, filename: str, runner: Runner) -> dict[str, Any]:
        """Start a job for ``path``; the manager owns the file from here on.

        Raises TooManyJobs, in which case the caller still owns ``path``.
        """
        with self._lock:
            self._prune_locked()
            active = sum(1 for j in self._jobs.values() if j.state not in TERMINAL_STATES)
            if active >= MAX_ACTIVE_JOBS:
                raise TooManyJobs(
                    f"{active} transcriptions are already in progress; "
                    "wait for one to finish"
                )
            job = TranscriptionJob(job_id=uuid.uuid4().hex, filename=filename, path=path)
            self._jobs[job.job_id] = job
            snapshot = job.snapshot()

        thread = threading.Thread(
            target=self._run,
            args=(job, runner),
            name=f"transcribe-{job.job_id[:8]}",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError as exc:
            self._finish(job, ERROR, error=f"Could not start transcription: {exc}")
            path.unlink(missing_ok=True)
            with self._lock:
                return job.snapshot()
        return snapshot

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def cancel(self, job_id: str) -> Optional[dict[str, Any]]:
        """Request cancellation.  The job stops at its next segment boundary;
        poll until its state is terminal."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state not in TERMINAL_STATES:
                job.cancel_event.set()
            return job.snapshot()

    def _run(self, job: TranscriptionJob, runner: Runner) -> None:
        def on_progress(fraction: float, text: str) -> None:
            with self._lock:
                if job.state == QUEUED:
                    job.state = RUNNING
                job.progress = max(job.progress, min(max(fraction, 0.0), 1.0))
                job.text = text

        try:
            if job.cancel_event.is_set():
                raise TranscriptionCancelled()
            result = runner(job.path, on_progress, job.cancel_event)
            self._finish(
                job,
                DONE,
                text=str(result.get("text") or ""),
                language=result.get("language"),
                duration=result.get("duration"),
            )
        except TranscriptionCancelled:
            self._finish(job, CANCELLED)
        except Exception as exc:  # the job thread is the last place to report it
            logger.exception("Local transcription job %s failed", job.job_id)
            self._finish(job, ERROR, error=f"Voice transcription failed: {exc}"[:500])
        finally:
            job.path.unlink(missing_ok=True)

    def _finish(self, job: TranscriptionJob, state: str, **fields: Any) -> None:
        with self._lock:
            job.state = state
            job.finished_at = time.time()
            if state == DONE:
                job.progress = 1.0
            for name, value in fields.items():
                setattr(job, name, value)

    def _prune_locked(self) -> None:
        cutoff = time.time() - RETENTION_SECONDS
        stale = [
            job_id for job_id, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in stale:
            del self._jobs[job_id]


job_manager = TranscriptionJobManager()
