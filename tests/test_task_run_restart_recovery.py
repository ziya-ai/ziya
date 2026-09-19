"""Restart reconciliation must leave a run the recovery UI can act on.

Run d2c18548 (2026-09-16) was swept by a sibling server's startup
reconciler.  Beyond the liveness bug (tests/test_task_run_executor_lock.py),
the reconciler wrote status="failed" and left every block_state frozen at
running/queued.  The frontend's recoveryTarget() anchors on either
``status == 'held'`` + ``held_at_block_id`` or on a block whose OWN state
is 'failed'; a restart-killed run had neither, so the tile showed no
recovery banner and the loudest control was Restart, which discards every
banked block.

These tests pin the record shape the reconciler must now produce: the
run is 'held' with held_reason='server_restart' and held_at_block_id set
to the innermost block that was executing -- the leaf, not its enclosing
group -- so a resume lands where the executor died.
"""

from pathlib import Path

import pytest

from app.models.task_run import TaskRunBlockState, TaskRunCreate
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def storage(tmp_path: Path) -> TaskRunStorage:
    return TaskRunStorage(tmp_path)


def _block(bid, btype, status, started_at=None, completed_at=None):
    return TaskRunBlockState(
        block_id=bid, block_type=btype, status=status,
        started_at=started_at, completed_at=completed_at,
    )


def _stranded_run(storage: TaskRunStorage, status: str = "running"):
    """Shape of d2c18548 at the moment it was swept: the root group and
    the serial loop it is executing are both 'running' (the loop started
    later), two earlier siblings are 'done', the rest are 'queued'."""
    run = storage.create(TaskRunCreate(card_id="gfx-stage-2"))
    storage.update_status(run.id, "running")
    if status != "running":
        storage.update_status(run.id, status)
    t0 = 1000.0
    storage.set_block_state(run.id, _block("b-root", "group", "running", t0))
    storage.set_block_state(run.id, _block("b-plan", "task", "done", t0, t0 + 1))
    storage.set_block_state(run.id, _block("b-queue", "task", "done", t0 + 1, t0 + 2))
    storage.set_block_state(run.id, _block("b-loop", "repeat", "running", t0 + 2))
    storage.set_block_state(run.id, _block("b-verify", "task", "queued"))
    return storage.get(run.id)


class TestRestartRecovery:
    def test_running_run_becomes_held_at_innermost_block(self, storage):
        run = _stranded_run(storage)
        assert storage.reconcile_stale_runs() == 1
        swept = storage.get(run.id)
        assert swept.status == "held"
        assert swept.held_reason == "server_restart"
        # The leaf, not the group that contains it: both are 'running',
        # the leaf started later.
        assert swept.held_at_block_id == "b-loop"
        assert "resume from the block" in (swept.error or "")
        # Banked work is untouched -- that is the whole point of 'held'.
        assert swept.block_states["b-plan"].status == "done"
        assert swept.block_states["b-queue"].status == "done"

    def test_paused_run_clears_pause_flag(self, storage):
        run = _stranded_run(storage)
        storage.request_pause(run.id)
        storage.update_status(run.id, "paused")
        assert storage.reconcile_stale_runs() == 1
        swept = storage.get(run.id)
        assert swept.status == "held"
        assert swept.pause_requested is False
        assert swept.cancel_requested is False

    def test_no_running_block_leaves_target_unset(self, storage):
        """A run swept before any block started (status 'queued', empty
        block_states) has no natural target; the reconciler must not
        invent one."""
        run = storage.create(TaskRunCreate(card_id="c"))
        storage.update_status(run.id, "queued")
        assert storage.reconcile_stale_runs() == 1
        swept = storage.get(run.id)
        assert swept.status == "held"
        assert swept.held_at_block_id is None

    def test_reconcile_is_idempotent(self, storage):
        run = _stranded_run(storage)
        assert storage.reconcile_stale_runs() == 1
        first = storage.get(run.id).model_dump()
        assert storage.reconcile_stale_runs() == 0
        assert storage.get(run.id).model_dump() == first

    def test_awaiting_input_keeps_its_own_reason(self, storage):
        """The pre-existing Ask branch is unchanged: a run holding at an
        Ask is held for 'awaiting_human_input', not 'server_restart'."""
        run = _stranded_run(storage)
        storage.open_ask(run.id, "b-loop", question="ok?")
        assert storage.get(run.id).status == "awaiting_input"
        assert storage.reconcile_stale_runs() == 1
        swept = storage.get(run.id)
        assert swept.status == "held"
        assert swept.held_reason == "awaiting_human_input"
        assert swept.held_at_block_id == "b-loop"
