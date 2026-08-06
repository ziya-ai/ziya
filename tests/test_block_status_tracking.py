"""Tests for per-block lifecycle status tracking — the run map's data.

Covers:
- done / failed / skipped statuses persisted to run.block_states
- continue policy: failed sibling recorded, later siblings still done
- inner-loop blocks are live-onlyContinuing the cut-off test-file diff exactly where it stopped:
  (the loop's iteration summaries are the durable record instead)
"""

import pytest
from types import SimpleNamespace

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block


class FakeStorage:
    """Records update_block_status calls; stubs the rest of the
    TaskRunStorage surface the executor touches."""

    def __init__(self):
        self.status_calls = []  # (block_id, status, error)
        self.artifacts = {}     # block_id -> persisted Artifact

    def get(self, run_id):
        # Must carry every flag the executor reads off a run record.  The
        # stub predates pause/step, so it lacked ``pause_requested`` and
        # ``step_budget`` — and ExecutionContext.pause_requested() reads
        # the former unconditionally, so all four status-tracking tests in
        # this file died with AttributeError before reaching a single
        # assertion.  Listing the flags explicitly (rather than reaching
        # for a permissive Mock) keeps the stub honest: the next field the
        # executor starts reading fails loudly here instead of silently
        # returning a truthy Mock and quietly changing what is tested.
        return SimpleNamespace(
            cancel_requested=False, pause_requested=False, step_budget=0,
        )

    def update_block_status(self, run_id, block_id, status, error=None, artifact=None):
        self.status_calls.append((block_id, status, error))
        if artifact is not None:
            self.artifacts[block_id] = artifact

    def write_iteration_artifact(self, *args, **kwargs):
        pass

    def append_iteration_summary(self, *args, **kwargs):
        pass


@pytest.fixture
def fake_tasks(monkeypatch):
    """Replace the model-invoking task executor with a recorder.
    A task whose name contains "FAIL" returns a failed artifact."""
    calls = []

    async def _fake(block, **kwargs):
        calls.append(block.name)
        return Artifact(
            summary=block.instructions or "",
            failed="FAIL" in (block.name or ""),
            created_at=0.0,
        )

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    return calls


def _task(id_, name):
    return Block(block_type="task", id=id_, name=name, instructions="x")


def _ctx(storage):
    # run_id="" disables relay emission; FakeStorage still receives the
    # persistence calls (they don't depend on run_id being non-empty).
    return ExecutionContext(run_id="", storage=storage)


def _statuses_for(storage, block_id):
    return [s for (b, s, _e) in storage.status_calls if b == block_id]


async def test_succeeding_blocks_marked_running_then_done(fake_tasks):
    storage = FakeStorage()
    grp = Block(block_type="group", id="g", body=[_task("t1", "one")])
    await execute_block(grp, _ctx(storage))
    assert _statuses_for(storage, "t1") == ["running", "done"]
    assert _statuses_for(storage, "g") == ["running", "done"]


async def test_failed_artifact_marks_block_failed(fake_tasks):
    storage = FakeStorage()
    await execute_block(_task("t1", "FAIL-one"), _ctx(storage))
    assert _statuses_for(storage, "t1") == ["running", "failed"]


async def test_raised_exception_marks_block_failed(monkeypatch):
    async def _boom(block, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bx, "execute_task_block", _boom)
    storage = FakeStorage()
    with pytest.raises(RuntimeError):
        await execute_block(_task("t1", "one"), _ctx(storage))
    assert _statuses_for(storage, "t1") == ["running", "failed"]
    assert storage.status_calls[-1][2] == "boom"


async def test_stop_policy_marks_skipped_siblings(fake_tasks):
    storage = FakeStorage()
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "one"),
        _task("t2", "FAIL-two"),
        _task("t3", "three"),
        _task("t4", "four"),
    ])
    await execute_block(grp, _ctx(storage))
    assert _statuses_for(storage, "t2") == ["running", "failed"]
    assert _statuses_for(storage, "t3") == ["skipped"]
    assert _statuses_for(storage, "t4") == ["skipped"]
    # The stopped sequence's artifact is the failed one — group failed.
    assert _statuses_for(storage, "g") == ["running", "failed"]


async def test_continue_policy_runs_all_and_records_failure(fake_tasks):
    storage = FakeStorage()
    grp = Block(block_type="group", id="g", body=[
        _task("t1", "one"),
        _task("t2", "FAIL-two"),
        _task("t3", "three"),
    ])
    await execute_block(grp, _ctx(storage))
    assert _statuses_for(storage, "t2") == ["running", "failed"]
    assert _statuses_for(storage, "t3") == ["running", "done"]
    assert "skipped" not in [s for (_b, s, _e) in storage.status_calls]
    # Sequence artifact is the last child's (passed) — group done.
    assert _statuses_for(storage, "g") == ["running", "done"]


async def test_inner_loop_blocks_are_live_only(fake_tasks):
    storage = FakeStorage()
    rpt = Block(
        block_type="repeat", id="r", repeat_mode="count", repeat_count=3,
        body=[_task("inner", "inner-task")],
    )
    await execute_block(rpt, _ctx(storage))
    # The inner task ran 3 times but its per-iteration status is never
    # persisted (binding_stack is non-empty inside an iteration) — the
    # loop's iteration_summaries carry the durable record instead.
    assert _statuses_for(storage, "inner") == []
    assert len(fake_tasks) == 3
    # The loop block itself is structural: persisted normally.
    assert _statuses_for(storage, "r") == ["running", "done"]


async def test_state_block_marked_done(fake_tasks):
    storage = FakeStorage()
    await execute_block(Block(block_type="state", id="s"), _ctx(storage))
    assert _statuses_for(storage, "s") == ["running", "done"]
