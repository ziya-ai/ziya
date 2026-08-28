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

from .base import BaseStorage, contained_path
from ..models.task_run import (
    TaskRun, TaskRunCreate, TaskRunBlockState, IterationSummary, ProgressNote,
)

# Cap on the retained progress trail.  Bounded because a long campaign
# emits a note per tool call: the trail is a readable narrative, not an
# audit log, and an unbounded list would grow the run file (rewritten in
# full on every heartbeat) without bound.  Oldest entries are evicted.
PROGRESS_NOTE_CAP = 200
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
        return contained_path(self.runs_dir, f"{run_id}.json")

    def read_run_file(self, path: str) -> Optional[dict]:
        """Read ONE run file by absolute path, returning the raw dict.

        Exists for the status-index cache, which is per-file by design: it
        re-reads only the records whose own mtime moved, so it needs a
        single-path reader rather than ``list()``.

        Returns the raw dict rather than a ``TaskRun``: the index needs four
        fields (status, source_conversation_id, root_run_id, attempt) and
        Pydantic-validating a ~108 KB record with nested block states and
        iteration summaries to read four of them is most of the cost this
        cache exists to avoid.  Decryption still happens — ``_read_json``
        owns that — so the saving is validation, not I/O.

        None for an unreadable or undecryptable file, matching the cache's
        contract: one bad record is skipped, not fatal to the index.
        """
        try:
            return self._read_json(Path(path))
        except Exception as e:  # noqa: BLE001 — one bad file is not fatal
            logger.debug(f"status-index: unreadable run file {path}: {e}")
            return None

    def _iteration_dir(self, run_id: str) -> Path:
        return contained_path(self.runs_dir, run_id) / "iterations"

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
            # Copied through explicitly: this constructor lists fields
            # one by one rather than splatting ``data``, so a new
            # TaskRunCreate field is silently dropped unless named here.
            # Needed by resume, which reconstructs ExecutionContext from
            # the run record alone.
            parameter_overrides=dict(data.parameter_overrides or {}),
            # Lineage.  root_run_id defaults to SELF so an initial run is
            # the root of its own lineage and the whole chain is always
            # one ``root_run_id`` filter — no parent-pointer walk, and no
            # null-root special case in the UI.
            root_run_id=data.root_run_id or run_id,
            parent_run_id=data.parent_run_id,
            attempt=max(1, data.attempt),
            resume_kind=data.resume_kind or "initial",
            resumed_from_block_id=data.resumed_from_block_id,
            # Mid-loop resume position.  Must be named explicitly for the
            # same reason parameter_overrides is: this constructor does not
            # splat ``data``, so an unnamed field is silently dropped and
            # the resume would quietly restart the loop at 0.
            resume_from_iteration=data.resume_from_iteration,
            resume_iteration_artifacts=dict(data.resume_iteration_artifacts or {}),
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def list_lineage(self, root_run_id: str) -> List[TaskRun]:
        """Every attempt sharing a lineage, oldest attempt first.

        Drives the tile's attempt rail.  Sorted by ``attempt`` rather
        than ``created_at`` so the displayed ordinals are monotonic even
        if two attempts land in the same millisecond.  Falls back to
        matching the id itself, so a run written before lineage tracking
        (``root_run_id`` absent) still returns itself rather than nothing.
        """
        out = [
            r for r in self.list()
            if (r.root_run_id or r.id) == root_run_id
        ]
        return sorted(out, key=lambda r: (r.attempt or 1, r.created_at))

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
        # "partial" is terminal: without it here completed_at is never
        # stamped, so the tile shows no runtime, and record_activity's
        # terminal guard keeps letting heartbeats through.
        # "held" is terminal for this run OBJECT for the same reasons —
        # the executor coroutine has unwound — even though the work is
        # continuable; the continuation is a NEW run.
        if status in ("done", "partial", "failed", "cancelled", "held"):
            run.completed_at = time.time()
        if error:
            run.error = error
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        # The sidebar's project-wide status index memoises on the run
        # DIRECTORY's mtime, and rewriting a file's contents does not
        # reliably bump that — so a run going running -> held would leave
        # every conversation row stale until some unrelated write happened
        # to create or delete a file.  Cheap: the index is process-local
        # and this only drops a dict reference.
        try:
            from ..utils.run_status_index import invalidate_for
            invalidate_for(str(self.runs_dir))
        except Exception:  # noqa: BLE001 — an indicator must never break a write
            pass
        return run

    def record_activity(
        self, run_id: str, note: Optional[str] = None,
        min_interval_s: float = 5.0,
        source: Optional[str] = None,
    ) -> Optional[TaskRun]:
        """Heartbeat: stamp ``last_activity_at`` (+ optional
        ``progress_note``) on the run file.

        Throttled to one disk write per ``min_interval_s`` per run so
        per-token text deltas don't become a write storm.  A call
        carrying a genuinely NEW note bypasses the throttle so the
        surfaced note is never stale relative to the last tool call.
        No-op for unknown or already-terminal runs.

        A note is ALSO appended to ``progress_notes``, the bounded trail
        that survives being overwritten.  ``source`` (``"model"`` for a
        ``<progress note=.../>`` tag, else None for a tool-derived line)
        is carried through so the UI can prefer the richer kind without
        discarding the other.
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
            # Append to the durable trail, skipping an exact consecutive
            # repeat: a loop running the same command every iteration would
            # otherwise fill the whole window with one line and evict the
            # phase notes that give the trail its value.
            if not run.progress_notes or run.progress_notes[-1].note != note:
                run.progress_notes.append(
                    ProgressNote(note=note, at=now, source=source)
                )
                if len(run.progress_notes) > PROGRESS_NOTE_CAP:
                    del run.progress_notes[:-PROGRESS_NOTE_CAP]
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
        elif status in ("done", "failed", "cancelled", "skipped", "held"):
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

    def record_call(
        self, run_id: str, block_id: str,
        call_snapshot: dict,
        block_scopes: Optional[dict] = None,
    ) -> Optional[TaskRun]:
        """Record a resolved Call target and its callee's block scopes.

        Both halves land in one read-merge-write because they are written
        at the same instant and a Call inside a loop body would otherwise
        cost two disk round-trips per iteration.

        Additive only: an existing ``call_snapshots`` entry or
        ``block_scopes`` key is never overwritten.  That is what preserves
        the audit-trail guarantee those records carry — appending a callee
        the run actually invoked is recording history, whereas replacing
        an existing entry would rewrite it.  It also makes a repeated call
        (same block, later loop iteration) naturally idempotent, and the
        no-op case skips the write entirely rather than rewriting the run
        file once per iteration.
        """
        run = self.get(run_id)
        if not run:
            return None
        dirty = False
        if block_id and block_id not in (run.call_snapshots or {}):
            run.call_snapshots[block_id] = call_snapshot
            dirty = True
        if block_scopes:
            # permissions_snapshot may be absent when its launch-time
            # capture failed (non-fatal by design).  Seed a minimal shell
            # rather than dropping the callee's scopes on the floor: a
            # partial audit trail beats a silently empty one.
            snap = run.permissions_snapshot
            if snap is None:
                snap = {"block_scopes": {}}
                run.permissions_snapshot = snap
            existing = snap.setdefault("block_scopes", {})
            for key, value in block_scopes.items():
                if key not in existing:
                    existing[key] = value
                    dirty = True
        if not dirty:
            return run
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

    def mark_held(
        self, run_id: str, reason: str = "",
        block_id: str = "", error: Optional[str] = None,
        faults: Optional[dict] = None, gate_reason: Optional[str] = None,
    ) -> Optional[TaskRun]:
        """Record that a run stopped on an infrastructure fault.

        Distinct from ``update_status(run_id, "held")`` only in that it
        also persists the fault kind and the block the run reached, so
        a later continuation does not have to infer the resume point
        from block_states.  Deliberately does NOT run the
        partial-reclassification pass: "partial" describes how much
        work got done, whereas "held" describes why it stopped, and
        collapsing the two would lose the actionable half (the
        infrastructure needs fixing, not the card).

        ``faults`` is the aggregate from infra_gate.summarize — the
        breadth of the collapse, written ONCE here rather than
        incremented per fault, because this class does unguarded
        read-modify-write and concurrent siblings would lose writes.
        """
        run = self.get(run_id)
        if not run:
            return None
        run.status = "held"  # type: ignore[assignment]
        run.held_reason = reason or None
        run.held_at_block_id = block_id or None
        if faults:
            run.held_faults = faults
        if gate_reason:
            run.held_gate_reason = gate_reason
        if error:
            run.error = error
        run.completed_at = time.time()
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        # Same reason as update_status: a hold is the single most important
        # transition for the sidebar to reflect promptly, and it rewrites a
        # file in place rather than adding one.
        try:
            from ..utils.run_status_index import invalidate_for
            invalidate_for(str(self.runs_dir))
        except Exception:  # noqa: BLE001
            pass
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
        # Drop any unspent step credit.  Resume means "run to
        # completion", so a leftover budget must not survive to let a
        # later re-pause silently slip a boundary.
        run.step_budget = 0
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def request_step(self, run_id: str, count: int = 1) -> Optional[TaskRun]:
        """Grant ``count`` boundary crossings to a paused run.

        Leaves ``pause_requested`` set, which is what distinguishes a
        step from a resume: the executor's wait-loop spends one credit
        to pass the boundary it is holding at, then holds again at the
        next one.  Credits accumulate, so calling this three times
        advances three boundaries.

        Also sets ``pause_requested`` if it was clear, so stepping a
        freely-running run is meaningful: it will advance to the next
        boundary and hold there rather than being a no-op.  Without
        this, a step on a running run would only be observed if the run
        happened to already be paused.
        """
        run = self.get(run_id)
        if not run:
            return None
        run.pause_requested = True
        run.step_budget = max(0, run.step_budget) + max(1, int(count))
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return run

    def consume_step(self, run_id: str) -> bool:
        """Spend one step credit if any remain.  Returns True if spent.

        Called from ``block_executor._wait_if_paused`` when it finds the
        pause flag set: a True return means "cross this one boundary
        and keep holding afterwards".  The decrement is persisted so
        the credit cannot be double-spent by a subsequent poll.

        Not atomic across processes — two executors sharing a run file
        could in principle both read the same credit.  Acceptable
        because a run's executor coroutine is single and process-local
        (see mark_active/is_active), so there is only ever one consumer.
        """
        run = self.get(run_id)
        if not run or run.step_budget <= 0:
            return False
        run.step_budget = run.step_budget - 1
        run.updated_at = int(time.time() * 1000)
        self._write_json(self._run_file(run_id), run.model_dump())
        return True

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

    def seed_replayed_iterations(
        self, run_id: str, block_id: str, summaries: List[IterationSummary],
        block_type: str = "repeat",
        create_if_missing: bool = False,
    ) -> None:
        """Install a resumed loop's replayed prefix in one write.

        Called at launch, before the executor coroutine is scheduled, so
        it cannot race the executor's own ``append_iteration_summary``
        calls — both rewrite the whole run file, so an interleaving would
        drop one side's records entirely.

        Existing summaries are preserved and the merged list is sorted by
        index, so this is safe to call on a block that already has
        records even though the launch path never does.  A same-index
        collision keeps the EXECUTED record: a summary this run produced
        is a better description of it than one carried from a prior
        attempt.
        """
        if not summaries:
            return
        run = self.get(run_id)
        if not run:
            return
        state = run.block_states.get(block_id)
        if state is None and create_if_missing:
            # A resume point inside a CALLED card has no state at launch:
            # _seed_block_states walks only the caller's tree, and the
            # callee's blocks are not seeded until the Call executes.
            # Returning here dropped the entire replayed prefix for
            # precisely the shape that banks the most work — a wide
            # parallel fan-out inside a Call — and the loss is not
            # cosmetic: the NEXT resume selects which iterations to bank
            # by reading these summaries, so their absence makes it
            # re-run work whose artifacts it is holding.
            #
            # Gated on an explicit opt-in rather than done unconditionally:
            # storage cannot tell a legitimate callee block from a stale or
            # misspelled id, and minting state for the latter puts a
            # phantom row in the run map for a block no card contains.  The
            # launch path passes True because its caller has already
            # resolved the id against the card snapshot AND the call
            # snapshots — so existence is established before storage is
            # asked to record it.
            state = TaskRunBlockState(
                block_id=block_id, block_type=block_type, status="queued",
            )
            run.block_states[block_id] = state
        if state is None:
            logger.debug(
                f"seed_replayed_iterations: block {block_id} not seeded; "
                f"skipping {len(summaries)} replayed records"
            )
            return
        executed = {s.index for s in state.iteration_summaries}
        merged = list(state.iteration_summaries) + [
            s for s in summaries if s.index not in executed
        ]
        merged.sort(key=lambda s: s.index)
        state.iteration_summaries = merged
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
