"""A child that RAISES must route through on_failure, not bypass it.

``on_failure`` is specified in terms of "the first child whose artifact
is failed" (design/task-cards.md §Failure policy).  A child that raises
has no artifact, so before the fix in ``_execute_sequence`` the
exception unwound every enclosing sequence straight to the run
boundary: ``stop`` and ``continue`` behaved identically (the run died),
remaining siblings were never marked skipped, and the outputs already
accumulated from earlier siblings were discarded with the artifact that
was never returned.

The two other containers already had these semantics —
``_execute_parallel`` records a failed child and ``_execute_repeat`` a
failed iteration, each re-raising ONLY infra faults — so these tests
pin the sequence path to the behaviour its siblings already had.

The distinction that must survive: a failure of the WORK converts to a
failed artifact, while an INFRA fault and a cancellation keep
propagating.  An infra fault is what lets api.task_cards mark the run
``held`` (resumable) rather than ``failed``, so converting one would
cost the run its resume position.
"""

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import (
    BlockExecutionCancelled,
    ExecutionContext,
    execute_block,
)
from app.agents.task_executor import TaskExecutorError, TaskInfraError
from app.models.task_card import Artifact, ArtifactPart, Block


@pytest.fixture
def raising_tasks(monkeypatch):
    """Task executor stand-in driven by the block's name.

    * ``RAISE``  — raises TaskExecutorError (a failure of the work)
    * ``INFRA``  — raises TaskInfraError (an environment fault)
    * ``CANCEL`` — raises BlockExecutionCancelled
    * anything else succeeds, emitting one output part so accumulation
      across a later failure is observable.
    """
    ran = []

    async def _fake(block, **kwargs):
        name = block.name or ""
        ran.append(name)
        if "RAISE" in name:
            raise TaskExecutorError(f"{name} exploded")
        if "INFRA" in name:
            raise TaskInfraError(
                f"{name} hit dead infra",
                infra_kind="transient_service_error",
                block_id=block.id or "",
            )
        if "CANCEL" in name:
            raise BlockExecutionCancelled()
        return Artifact(
            summary=name,
            outputs=[ArtifactPart(part_type="text", text=f"out:{name}")],
            failed=False,
            created_at=0.0,
        )

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    return ran


def _task(id_, name):
    return Block(block_type="task", id=id_, name=name, instructions=name)


def _ctx():
    # storage=None short-circuits persistence/cancel polling;
    # run_id="" disables relay emission.
    return ExecutionContext(run_id="")


# ── a raising child is subject to on_failure ──────────────────

async def test_raising_child_with_stop_halts_without_escaping(raising_tasks):
    """stop: the sequence ends at the raiser and returns a failed artifact."""
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "first"),
        _task("t2", "RAISE-mid"),
        _task("t3", "never"),
    ])
    artifact = await execute_block(grp, _ctx())

    assert artifact.failed is True
    assert raising_tasks == ["first", "RAISE-mid"], (
        "stop must not run siblings after the raiser"
    )
    assert any("remaining step(s) skipped" in d for d in artifact.decisions), (
        "the skip note proves the on_failure=stop path ran, rather than the "
        "exception merely being swallowed"
    )


async def test_raising_child_with_continue_runs_later_siblings(raising_tasks):
    """continue (the default): a raiser no longer tears down the sequence."""
    grp = Block(block_type="group", id="g", body=[
        _task("t1", "first"),
        _task("t2", "RAISE-mid"),
        _task("t3", "after"),
    ])
    artifact = await execute_block(grp, _ctx())

    assert raising_tasks == ["first", "RAISE-mid", "after"]
    # Last-wins on ``failed``: the final sibling succeeded, so the
    # sequence artifact is not failed — identical to how a returned
    # failed artifact behaves under continue.
    assert artifact.failed is False
    assert any("raised" in d for d in artifact.decisions), (
        "the failure must still be recorded in the audit trail"
    )


async def test_outputs_before_a_raising_child_survive(raising_tasks):
    """Earlier siblings' declared outputs are no longer lost."""
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "alpha"),
        _task("t2", "beta"),
        _task("t3", "RAISE-late"),
    ])
    artifact = await execute_block(grp, _ctx())

    texts = [p.text for p in artifact.outputs]
    assert texts == ["out:alpha", "out:beta"], (
        "an escaping exception previously discarded the whole artifact, "
        "taking every earlier sibling's emitted output with it"
    )


# ── what must still propagate ─────────────────────────────────

async def test_infra_fault_still_propagates(raising_tasks):
    """An infra fault must NOT be converted: the run has to hold."""
    grp = Block(block_type="group", id="g", on_failure="continue", body=[
        _task("t1", "first"),
        _task("t2", "INFRA-mid"),
        _task("t3", "never"),
    ])
    with pytest.raises(TaskExecutorError) as excinfo:
        await execute_block(grp, _ctx())

    assert getattr(excinfo.value, "infra_kind", "") == "transient_service_error"
    assert raising_tasks == ["first", "INFRA-mid"], (
        "an infra fault must abort the sequence even under continue"
    )


async def test_cancellation_still_propagates(raising_tasks):
    """A stop request is not a work failure."""
    grp = Block(block_type="group", id="g", on_failure="continue", body=[
        _task("t1", "first"),
        _task("t2", "CANCEL-mid"),
        _task("t3", "never"),
    ])
    with pytest.raises(BlockExecutionCancelled):
        await execute_block(grp, _ctx())

    assert raising_tasks == ["first", "CANCEL-mid"]


async def test_nested_group_raise_is_gated_at_each_level(raising_tasks):
    """The conversion happens per sequence, so an outer stop also fires."""
    inner = Block(block_type="group", id="inner", on_failure="stop", body=[
        _task("i1", "RAISE-inner"),
        _task("i2", "inner-never"),
    ])
    outer = Block(block_type="group", id="outer", on_failure="stop", body=[
        _task("o1", "outer-first"),
        inner,
        _task("o2", "outer-never"),
    ])
    artifact = await execute_block(outer, _ctx())

    assert artifact.failed is True
    assert raising_tasks == ["outer-first", "RAISE-inner"]
