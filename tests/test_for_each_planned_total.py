"""A for_each Repeat must persist its resolved roster size.

The run map trails each loop row's iteration dot strip with a count.
For a for_each fan-out that count alone answers "how many have run" but
not "out of how many": the roster is resolved at RUN time from the
templated source, so neither the card definition nor the frontend can
compute the denominator.  The executor is the only party that ever
knows it — it already emitted the number as ``planned`` on the
``block_started`` event, but nothing persisted it, so the figure was
gone after a reload and unknowable for a partial run.

These pin the persistence seam end-to-end: executing the loop writes
``planned_iterations`` onto the loop's block state (post-``repeat_max``
clipping, since that is what will actually run), while count-mode loops
— whose total the card already states — leave the field alone.
"""

import asyncio
import json
import time

from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.storage.task_runs import TaskRunStorage
import app.agents.block_executor as be
from app.agents.block_executor import ExecutionContext

LOOP_ID = "b-fanout"


def _roster(n: int) -> str:
    """A literal for_each source, as a planner task's summary would carry."""
    return json.dumps([f"item-{i:02d}" for i in range(n)])


def _for_each_loop(n: int, ceiling: int | None = None) -> Block:
    return Block(
        block_type="repeat", id=LOOP_ID, name="fan-out",
        repeat_mode="for_each",
        repeat_for_each_source=_roster(n),
        repeat_max=ceiling,
        body=[Block(block_type="task", id="b-inner", name="inner",
                    instructions="audit {{item}}")],
    )


def _count_loop(n: int) -> Block:
    return Block(
        block_type="repeat", id=LOOP_ID, name="counted",
        repeat_mode="count", repeat_count=n,
        body=[Block(block_type="task", id="b-inner", name="inner",
                    instructions="do it")],
    )


def _wired(tmp_path):
    """Real storage with the loop's block state seeded, as launch does."""
    st = TaskRunStorage(tmp_path)
    run = st.create(TaskRunCreate(card_id="c1"))
    st.set_block_state(run.id, TaskRunBlockState(
        block_id=LOOP_ID, block_type="repeat", status="running",
    ))
    ctx = ExecutionContext(
        run_id=run.id, project_id="p", project_root=str(tmp_path),
        storage=st,
    )
    return st, run, ctx


async def _run(block: Block, ctx: ExecutionContext) -> Artifact:
    """Execute the loop with the task body stubbed out."""
    async def _work(blk, **kwargs):
        return Artifact(summary="ok", created_at=time.time())

    with patch.object(be, "execute_task_block", _work):
        return await be.execute_block(block, ctx)


class TestPlannedTotalIsPersisted:
    """The executor→storage seam: the run record carries the denominator."""

    def test_for_each_records_the_roster_size(self, tmp_path):
        st, run, ctx = _wired(tmp_path)
        asyncio.run(_run(_for_each_loop(4), ctx))
        state = st.get(run.id).block_states[LOOP_ID]
        assert state.planned_iterations == 4, (
            "the for_each loop's block state does not carry the resolved "
            "roster size; the run map has no 'm' for its n/m figure"
        )
        # Positive control that the loop actually ran — without it the
        # assertion above could be satisfied by a stub that never
        # executed anything.
        assert len(state.iteration_summaries) == 4

    def test_a_clipped_roster_records_what_will_run(self, tmp_path):
        """repeat_max clips the roster; 'm' must be the dispatched count.

        Recording the unclipped size would make a fully-successful
        clipped run read as forever incomplete (2/6 done, nothing left
        running).  The clipping itself is separately surfaced by the
        roster-truncation decision.
        """
        st, run, ctx = _wired(tmp_path)
        asyncio.run(_run(_for_each_loop(6, ceiling=2), ctx))
        assert st.get(run.id).block_states[LOOP_ID].planned_iterations == 2

    def test_count_mode_leaves_the_field_unset(self, tmp_path):
        """Non-vacuity: the field tracks for_each, not planning per se.

        count mode's total is readable from the card, and stamping it
        here would silently extend the n/m rendering to a shape the
        change deliberately does not target.
        """
        st, run, ctx = _wired(tmp_path)
        asyncio.run(_run(_count_loop(3), ctx))
        state = st.get(run.id).block_states[LOOP_ID]
        assert state.planned_iterations is None
        # The loop itself still ran — the absence is a choice, not a
        # side effect of the loop failing to execute.
        assert len(state.iteration_summaries) == 3


class TestStorageMethod:
    """set_block_planned_iterations degrades silently, like its siblings."""

    def test_unseeded_block_is_a_noop(self, tmp_path):
        st = TaskRunStorage(tmp_path)
        run = st.create(TaskRunCreate(card_id="c1"))
        st.set_block_planned_iterations(run.id, "ghost", 5)
        assert "ghost" not in st.get(run.id).block_states, (
            "recording a planned total must not mint block state for a "
            "block no card contains"
        )

    def test_missing_run_is_a_noop(self, tmp_path):
        st = TaskRunStorage(tmp_path)
        # Must not raise — the executor treats the write as best-effort.
        st.set_block_planned_iterations("no-such-run", LOOP_ID, 5)

    def test_old_records_load_with_none(self):
        """Runs written before the field existed default it to None."""
        state = TaskRunBlockState(block_id="b", block_type="repeat")
        assert state.planned_iterations is None
