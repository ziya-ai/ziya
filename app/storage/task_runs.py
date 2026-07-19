"""
Task run storage — file-per-run under a project directory.

Follows the same pattern as TaskCardStorage.  Runs are ephemeral
but persist enough for the frontend to poll status and read final
artifacts across reloads.
"""

import json
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from .base import BaseStorage
from ..models.task_run import TaskRun, TaskRunCreate, TaskRunBlockState, IterationSummary
from ..models.task_card import Artifact

logger = logging.getLogger(__name__)


class TaskRunStorage(BaseStorage[TaskRun]):
    """CRUD for TaskRuns scoped to a project."""

    def __init__(self, project_dir: Path):
        self.runs_dir = project_dir / "task_runs"
        super().__init__(self.runs_dir)
        # Process-local registry of run_ids whose ``_run`` coroutine is
        # currently executing in this server.  Server restarts wipe the
        # set; the on-disk ``status`` is the durable record.  Used by
        # the cancel endpoint to distinguish between "live executor —
        # set the flag, the loop will honor it" and "zombie run from a
        # prior server lifetime — force-cancel directly".
        self._active_runs: set[str] = set()
        # Per-run wall-clock of the last heartbeat WRITE, for the
        # record_activity throttle.  Process-local by design.
        self._last_activity_write: Dict[str, float] = {}

    def _run_file(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def _iteration_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id / "iterations"

    def _iteration_file(self, run_id: str, block_id: str, index: int) -> Path:
        return self._iteration_dir(run_id) / f"{block_id}_{index}.json"

    def get(self, run_id: str) -> Optional[TaskRun]:
        data = self._read_json(self._run_file(run_id))
        if data:
            return TaskRun(**data)
        return None

    def list(self, card_id: Optional[str] = None) -> List[TaskRun]:
        runs: List[TaskRun] = []
        if self.runs_dir.exists():
            for run_file in self.runs_dir.glob("*.json"):
                data = self._read_json(run_file)
                if data:
                    try:
                        run = TaskRun(**data)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Skipping corrupt task run {run_file}: {e}")
                        continue
                    if card_id and run.card_id != card_id:
                        continue
                    runs.append(run)
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def create(self, data: TaskRunCreate) -> TaskRun:
        run_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        run = TaskRun(
            id=run_id,
            card_id=data.card_id,
            source_conversation_id=data.source_conversation_id,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def update_status(
        self, run_id: str, status: str,
        error: Optional[str] = None,
    ) -> Optional[TaskRun]:
        run = self.get(run_id)
        if not run:
            return None
        run.status = status  # type: ignore[assignment]
        if status == "running" and run.started_at is None:
            run.started_at = time.time()
        if status in ("done", "failed", "cancelled"):
            run.completed_at = time.time()
        if error:
            run.error = error
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def record_activity(
        self, run_id: str, note: Optional[str] = None,
        min_interval_s: float = 5.0,
    ) -> Optional[TaskRun]:
        """Heartbeat: stamp ``last_activity_at`` (+ optional
        ``progress_note``) on the run file.

        Throttled to one disk write per ``min_interval_s`` per run so
        per-token text deltas don't become a write storm.  A call
        carrying a genuinely NEW note bypasses the throttle so the
        surfaced note is never stale relative to the last tool call.
        No-op for unknown or already-terminal runs.
        """
        now = time.time()
        last = self._last_activity_write.get(run_id, 0.0)
        # Cheap path: throttled, note-less heartbeat — skip the disk
        # read entirely.
        if note is None and (now - last) < min_interval_s:
            return None
        run = self.get(run_id)
        if not run:
            return None
        if run.status not in ("queued", "running"):
            # Never resurrect activity on a terminal run.
            return None
        if note is not None and note == run.progress_note \
                and (now - last) < min_interval_s:
            return None
        run.last_activity_at = now
        if note is not None:
            run.progress_note = note
        run.updated_at = int(now * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        self._last_activity_write[run_id] = now
        return run

    def set_artifact(
        self, run_id: str, artifact: Artifact,
    ) -> Optional[TaskRun]:
        run = self.get(run_id)
        if not run:
            return None
        run.artifact = artifact
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def set_block_state(
        self, run_id: str, state: TaskRunBlockState,
    ) -> Optional[TaskRun]:
        run = self.get(run_id)
        if not run:
            return None
        run.block_states[state.block_id] = state
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def update_block_status(
        self, run_id: str, block_id: str, status: str,
        error: Optional[str] = None,
        artifact: Optional[Artifact] = None,
    ) -> None:
        """Update one block's lifecycle status in place, preserving its
        iteration_summaries (unlike set_block_state, which replaces the
        whole state object).  Called by the block executor as each
        structural block starts/finishes so the REST snapshot can drive
        the run map after reload/reconnect.

        Timestamps: started_at is set on the first transition to
        "running"; completed_at on any terminal status (done / failed /
        cancelled / skipped).
        """
        run = self.get(run_id)
        if not run:
            return
        state = run.block_states.get(block_id)
        if state is None:
            return
        state.status = status  # type: ignore[assignment]
        now = time.time()
        if status == "running" and state.started_at is None:
            state.started_at = now
        elif status in ("done", "failed", "cancelled", "skipped"):
            state.completed_at = now
        if error:
            state.error = error[:500]
        # Persist the block's own artifact on the terminal write so its
        # output survives reload.  Loop *iterations* already persist via
        # write_iteration_artifact; this covers structural blocks
        # (planner, verifier, the loop container's summary) which
        # otherwise left only status behind.
        if artifact is not None:
            state.artifact = artifact
        run.block_states[block_id] = state
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())

    def set_permissions_snapshot(
        self, run_id: str, snapshot: dict,
    ) -> Optional[TaskRun]:
        """Record the effective permissions captured at launch time.

        The snapshot is opaque to storage — its schema is defined in
        ``app/utils/permissions_snapshot.py``.  Only set once per run,
        immediately after creation; later updates would defeat the
        audit-trail purpose."""
        run = self.get(run_id)
        if not run:
            return None
        run.permissions_snapshot = snapshot
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def set_card_snapshot(
        self, run_id: str, snapshot: dict,
    ) -> Optional[TaskRun]:
        """Record the card definition (name/description/root) captured at
        launch.  Set once per run, immediately after creation, so later
        edits to the card don't rewrite what this run is shown to have
        executed."""
        run = self.get(run_id)
        if not run:
            return None
        run.card_snapshot = snapshot
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def update(self, run_id: str, data) -> Optional[TaskRun]:
        """BaseStorage contract.  Task runs don't have a generic update
        path — use update_status / set_artifact / set_block_state for
        semantic mutations.  This method is here only to satisfy the
        abstract base class."""
        raise NotImplementedError(
            "TaskRun does not support generic update; use update_status, "
            "set_artifact, or set_block_state"
        )

    def request_cancel(self, run_id: str) -> Optional[TaskRun]:
        """Set the soft-cancel flag on a running run."""
        run = self.get(run_id)
        if not run:
            return None
        run.cancel_requested = True
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def request_pause(self, run_id: str) -> Optional[TaskRun]:
        """Set the soft-pause flag on a run.  Status is NOT flipped to
        "paused" here — the executor does that when it actually reaches
        the next boundary (via block_executor._wait_if_paused), so the
        run reads "running" (pending) until the hold takes effect."""
        run = self.get(run_id)
        if not run:
            return None
        run.pause_requested = True
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def request_resume(self, run_id: str) -> Optional[TaskRun]:
        """Clear the soft-pause flag.  The executor's wait-loop observes
        this at its next poll and restores status to "running"."""
        run = self.get(run_id)
        if not run:
            return None
        run.pause_requested = False
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    # ---- live-run registry (process-local, not persisted) ----------

    def mark_active(self, run_id: str) -> None:
        """Record that ``run_id``'s executor coroutine is alive in this
        process.  Called from the start of ``_run`` in the launch path."""
        self._active_runs.add(run_id)

    def mark_inactive(self, run_id: str) -> None:
        """Drop ``run_id`` from the live-run set.  Called from the
        ``finally`` block of ``_run`` so the entry is removed even if
        the executor errors out."""
        self._active_runs.discard(run_id)

    def is_active(self, run_id: str) -> bool:
        """Return True iff ``run_id``'s executor is currently running
        in this process."""
        return run_id in self._active_runs

    # ---- startup reconciliation -----------------------------------

    def reconcile_stale_runs(self) -> int:
        """Sweep on-disk runs and mark any ``running`` / ``queued`` /
        ``paused``
        rows as ``failed`` — they were owned by a prior server lifetime
        and have no live executor.  Idempotent.  Safe to call at
        startup before any new runs are launched.

        Returns the count of runs reconciled.
        """
        reconciled = 0
        now_ms = int(time.time() * 1000)
        for run in self.list():
            if run.status not in ("running", "queued", "paused"):
                continue
            run.status = "failed"  # type: ignore[assignment]
            run.cancel_requested = False
            run.error = (
                "Run did not survive a server restart.  The executor "
                "was terminated mid-flight; this record was reconciled "
                "at the next server start."
            )
            if run.completed_at is None:
                run.completed_at = time.time()
            run.updated_at = now_ms
            self._write_json(self._run_file(run.id), run.model_dump())
            reconciled += 1
        return reconciled

    def append_iteration_summary(
        self, run_id: str, block_id: str, summary: IterationSummary,
    ) -> None:
        """Append a summary to the given block's iteration_summaries list.
        Called once per iteration of a Repeat block."""
        run = self.get(run_id)
        if not run:
            return
        state = run.block_states.get(block_id)
        if state is None:
            return
        state.iteration_summaries.append(summary)
        run.block_states[block_id] = state
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())

    def write_iteration_artifact(
        self, run_id: str, block_id: str, index: int, artifact: Artifact,
    ) -> None:
        """Persist the full Artifact for a single iteration to disk.
        Each iteration file is small (~10KB typical), scales linearly
        with retained iterations (failures + first 50 passes per
        Repeat).  See design/task-cards.md §Iteration result storage.

        Uses the encryption-aware _write_json() helper (same as every
        other TaskRunStorage write) so iteration artifacts fall under
        the "session_data" ALE category instead of being written as
        plaintext JSON [PenPal #43, CWE-311]."""
        path = self._iteration_file(run_id, block_id, index)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, artifact.model_dump(mode="json"))

    def read_iteration_artifact(
        self, run_id: str, block_id: str, index: int,
    ) -> Optional[Artifact]:
        path = self._iteration_file(run_id, block_id, index)
        try:
            data = self._read_json(path)
            if data is None:
                return None
            return Artifact(**data)
        except (TypeError, ValueError) as e:
            logger.warning(f"Could not read iteration artifact {path}: {e}")
            return None

    def delete(self, run_id: str) -> bool:
        run_file = self._run_file(run_id)
        # Also clean up the per-iteration directory if it exists.
        iter_dir = self.runs_dir / run_id
        if iter_dir.exists() and iter_dir.is_dir():
            try:
                for sub in iter_dir.rglob("*"):
                    if sub.is_file():
                        sub.unlink()
                for sub in sorted(iter_dir.rglob("*"), reverse=True):
                    if sub.is_dir():
                        sub.rmdir()
                iter_dir.rmdir()
            except OSError as e:
                logger.warning(f"Could not remove iteration dir for {run_id}: {e}")
        if not run_file.exists():
            return False
        run_file.unlink()
        return True
