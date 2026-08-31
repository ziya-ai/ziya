"""``repeat_max`` truncating a for_each roster must leave a record.

The defect, measured on a real run: a Stage 1 task emitted a roster of
112 capability ids; the fan-out below it carried ``repeat_max=60``;
``_plan_iterations`` did ``items = items[:60]`` and returned.  Fifty-two
items were never dispatched and NOTHING said so — not the block artifact,
not the run record, not the logs.  The downstream merge stage reads the
output DIRECTORY, so it saw only what ran and reported a complete pass
over 54% of the queue.

Truncation itself is legitimate and is deliberately preserved here:
``repeat_max`` is a cost ceiling, and silently exceeding it would be
worse than clipping.  What is not legitimate is clipping INVISIBLY.  A
run whose scope was cut must be able to say so afterwards, because the
alternative is a report that overstates its own coverage — the one
failure mode a study cannot recover from after the fact.

Every assertion here pins visibility, not behaviour change:

  * the dispatched count is still exactly ``repeat_max`` (unchanged), and
  * the block's artifact carries a decision naming BOTH numbers, and
  * a roster that fits produces no such decision — without which the
    tests above would pass against code that annotated every loop.
"""

import asyncio
import json
import time

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.storage.task_runs import TaskRunStorage
import app.agents.block_executor as be
from app.agents.block_executor import ExecutionContext, _plan_iterations

ROSTER = 112
CEILING = 60
LOOP_ID = "b-gapfanout"


def _roster(n: int) -> str:
    """A literal for_each source, as a planner task's summary would carry."""
    return json.dumps([f"gap-{i:03d}" for i in range(n)])


def _loop(roster_size: int, ceiling: int | None) -> Block:
    return Block(
        block_type="repeat", id=LOOP_ID, name="Stage 2: per-gap second look",
        repeat_mode="for_each",
        repeat_for_each_source=_roster(roster_size),
        repeat_max=ceiling,
        repeat_parallel=True, repeat_propagate="none",
        body=[Block(block_type="task", id="b-stage-a", name="Stage A",
                    instructions="audit {{item}}")],
    )


def _ctx(**kw) -> ExecutionContext:
    return ExecutionContext(
        run_id="r-trunc", project_id="p", project_root="/tmp", **kw)


async def _run_loop(block: Block, ctx: ExecutionContext) -> Artifact:
    """Execute the loop with the task body stubbed out."""
    async def _work(blk, **kwargs):
        return Artifact(summary="ok", created_at=time.time())

    with patch.object(be, "execute_task_block", _work), \
         patch.object(be, "_record_iteration", lambda *a, **k: asyncio.sleep(0)):
        return await be.execute_block(block, ctx)


class TestTheClippingItselfIsUnchanged:
    """Visibility must not become a behaviour change."""

    def test_only_the_ceiling_is_planned(self):
        iters = _plan_iterations(_loop(ROSTER, CEILING), _ctx())
        assert len(iters) == CEILING

    def test_the_planned_items_are_the_roster_head(self):
        """Pins WHICH items survive, so the record can name the rest.

        Without this the decision could report a truncation count while
        the surviving set was arbitrary, making the un-run remainder
        unrecoverable even with the count in hand.
        """
        iters = _plan_iterations(_loop(ROSTER, CEILING), _ctx())
        assert iters[0]["item"] == "gap-000"
        assert iters[-1]["item"] == f"gap-{CEILING - 1:03d}"

    def test_a_roster_within_the_ceiling_is_untouched(self):
        iters = _plan_iterations(_loop(40, CEILING), _ctx())
        assert len(iters) == 40

    def test_no_ceiling_dispatches_the_whole_roster(self):
        iters = _plan_iterations(_loop(ROSTER, None), _ctx())
        assert len(iters) == ROSTER

    def test_planning_without_a_ctx_still_works(self):
        """``_plan_iterations`` is called with ctx=None in unit paths.

        Recording onto ctx must therefore be optional, or adding the
        record would turn a working call into an AttributeError.
        """
        iters = _plan_iterations(_loop(ROSTER, CEILING))
        assert len(iters) == CEILING


class TestTheTruncationIsRecordedOnTheContext:
    """The mechanism the artifact decision is built from."""

    def test_a_clipped_roster_records_both_numbers(self):
        ctx = _ctx()
        _plan_iterations(_loop(ROSTER, CEILING), ctx)
        got = getattr(ctx, "roster_truncations", {}).get(LOOP_ID)
        assert got is not None, (
            "planning clipped 52 of 112 items and recorded nothing on the "
            "context; the block artifact has no way to report its own "
            "reduced scope"
        )
        assert got["roster"] == ROSTER
        assert got["dispatched"] == CEILING

    def test_a_fitting_roster_records_nothing(self):
        """Non-vacuity: proves the record tracks truncation, not planning.

        A test that only asserted presence would pass against code that
        annotated every for_each loop, making the decision noise that
        readers learn to ignore.
        """
        ctx = _ctx()
        _plan_iterations(_loop(40, CEILING), ctx)
        assert getattr(ctx, "roster_truncations", {}) == {}

    def test_two_loops_record_independently(self):
        """Keyed by block id, so one loop's clip is not read as another's.

        Asserts the counts field-by-field rather than by whole-dict
        equality: the record grew a ``dropped`` list of the un-run
        identities (so a follow-up pass can run exactly the remainder
        instead of the whole roster), and an equality assertion would
        have to be rewritten every time the record gains a field while
        testing nothing extra in exchange.
        """
        ctx = _ctx()
        _plan_iterations(_loop(ROSTER, CEILING), ctx)
        other = _loop(90, 20)
        other.id = "b-other"
        _plan_iterations(other, ctx)
        got = getattr(ctx, "roster_truncations", {})
        assert got[LOOP_ID]["roster"] == ROSTER
        assert got["b-other"]["roster"] == 90
        assert got["b-other"]["dispatched"] == 20
        # The identities are per-loop too, not pooled across loops: a
        # shared list would make the remainder of one loop unrecoverable
        # by mixing in another's, which is the whole point of the field.
        assert got["b-other"]["dropped"] == [f"gap-{i:03d}" for i in range(20, 90)]
        assert got[LOOP_ID]["dropped"][0] == f"gap-{CEILING:03d}"


class TestTheBlockArtifactCarriesTheDecision:
    """The outermost in-process surface: what the run record shows."""

    @pytest.mark.asyncio
    async def test_the_decision_is_present_and_names_both_counts(self):
        art = await _run_loop(_loop(ROSTER, CEILING), _ctx())
        hits = [d for d in (art.decisions or []) if "repeat_max" in d]
        assert hits, (
            f"the loop's artifact records no truncation decision; its "
            f"decisions were {art.decisions!r}.  A downstream stage reading "
            f"this run cannot tell that 52 of 112 items never ran."
        )
        text = " ".join(hits)
        assert str(ROSTER) in text and str(CEILING) in text, (
            f"the decision must name the roster size and the dispatched "
            f"count so the un-run remainder is recoverable; got {text!r}"
        )

    @pytest.mark.asyncio
    async def test_a_fitting_roster_adds_no_decision(self):
        art = await _run_loop(_loop(40, CEILING), _ctx())
        assert not [d for d in (art.decisions or []) if "repeat_max" in d]

    @pytest.mark.asyncio
    async def test_the_iterations_still_all_ran(self):
        """The decision must not be bought by running fewer iterations."""
        ran: list = []

        async def _work(blk, **kwargs):
            from app.context import get_task_iteration_context
            ran.append((get_task_iteration_context() or {}).get("index"))
            return Artifact(summary="ok", created_at=time.time())

        with patch.object(be, "execute_task_block", _work), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            await be.execute_block(_loop(ROSTER, CEILING), _ctx())
        assert len(ran) == CEILING


class TestTheDecisionReachesThePersistedRunRecord:
    """The SEAM.  An in-memory decision nobody stores is not a record.

    ``execute_block`` persists a block's artifact through
    ``update_block_status(artifact=...)``, so this asserts on what a
    later reader — the merge stage, the run map, a resume — actually
    finds on disk, rather than on the object the loop returned.
    """

    @pytest.mark.asyncio
    async def test_the_truncation_survives_to_block_states(self, tmp_path):
        storage = TaskRunStorage(tmp_path)
        run = storage.create(TaskRunCreate(card_id="card-trunc"))
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id=LOOP_ID, block_type="repeat", status="queued",
        ))
        ctx = ExecutionContext(
            run_id=run.id, project_id="p", project_root=str(tmp_path),
            storage=storage,
        )
        await _run_loop(_loop(ROSTER, CEILING), ctx)

        state = (storage.get(run.id).block_states or {}).get(LOOP_ID)
        assert state is not None and state.artifact is not None, (
            "the loop persisted no artifact, so nothing about its scope "
            "reached the run record"
        )
        hits = [d for d in (state.artifact.decisions or [])
                if "repeat_max" in d]
        assert hits, (
            f"the persisted artifact carries no truncation decision; a "
            f"reader of this run still cannot tell that 52 of 112 items "
            f"never ran.  Persisted decisions: "
            f"{state.artifact.decisions!r}"
        )
