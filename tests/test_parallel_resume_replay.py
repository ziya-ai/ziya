"""Parallel fan-out resume: the replayed prefix is NOT honoured.

Documents a real gap found while verifying a UI claim, rather than
asserting desired behaviour that does not exist.

Two separate facts, both surprising, and they interact badly:

1. ``resolve_iteration_resume`` REFUSES a mid-loop resume for a parallel
   loop (422).  That refusal is correct and well-reasoned: parallel
   iterations cannot see each other, so "resume at 3" has no ordering
   meaning and would just run fewer iterations while reporting the loop
   complete.

2. But BLOCK-level resume is still offered for the loop, and the parallel
   dispatch branch of ``_execute_repeat`` ignores ``resume_at`` entirely --
   it builds its task list from ``range(len(iterations))`` unconditionally,
   while only the serial branch consults the replayed prefix.

The consequence for the case this was checked against: a 20-wide auditor
fan-out that held on an expired credential after 18 subagents had already
produced artifacts re-runs ALL 20 on resume.  For a frontier-tier fan-out
that is the most expensive silent behaviour in the task system, and the
hold surface's promise of preserved progress is not kept for the one
block shape most likely to hold.

These tests pin the CURRENT behaviour so the gap is visible and a fix is
detectable.  ``test_parallel_branch_ignores_resume_at`` is the one that
should be inverted when the dispatch branch learns to skip banked
iterations; it deliberately fails loudly rather than xfail-ing, because a
silently-tolerated cost regression is how this survived unnoticed.
"""

import asyncio
import time

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
import app.agents.block_executor as be
from app.utils.resume_targets import resolve_iteration_resume


def _fanout(n: int, parallel: bool) -> Block:
    return Block(
        id="fanout", block_type="repeat", name="auditors",
        repeat_mode="count", repeat_count=n, repeat_parallel=parallel,
        repeat_propagate="none",
        body=[Block(id="t", block_type="task", name="a", instructions="go")],
    )


def _ctx_with_banked(n_banked: int):
    ctx = be.ExecutionContext(
        run_id="r-resume", project_id="p", project_root="/tmp",
    )
    ctx.resume_from_block_id = "fanout"
    ctx.resume_from_iteration = n_banked
    ctx.resume_iteration_artifacts = {
        i: Artifact(summary=f"banked {i}", created_at=time.time())
        for i in range(n_banked)
    }
    return ctx


async def _run(block: Block, ctx) -> list:
    """Execute, returning the iteration indices that actually ran."""
    ran: list = []

    async def _work(blk, **kw):
        from app.context import get_task_iteration_context
        idx = (get_task_iteration_context() or {}).get("index")
        ran.append(idx)
        return Artifact(summary=f"ran {idx}", created_at=time.time())

    async def _noop_record(*a, **k):
        return None

    with patch.object(be, "execute_task_block", _work), \
         patch.object(be, "_record_iteration", _noop_record):
        await be.execute_block(block, ctx)
    return sorted(i for i in ran if i is not None)


class TestMidLoopResumeRefusesParallel:
    """Fact 1: the iteration-level API correctly refuses."""

    def test_parallel_loop_is_refused(self):
        root = _fanout(6, parallel=True).model_dump()
        start, err = resolve_iteration_resume(
            root, "fanout", 4,
            [{"index": i, "status": "passed", "has_artifact": True}
             for i in range(6)],
            "continue_iteration",
        )
        assert start is None
        assert err is not None
        assert "parallel" in err.lower()

    def test_serial_loop_is_allowed(self):
        root = _fanout(6, parallel=False).model_dump()
        start, err = resolve_iteration_resume(
            root, "fanout", 3,
            [{"index": i, "status": "passed", "has_artifact": True}
             for i in range(6)],
            "continue_iteration",
        )
        assert err is None
        assert start == 4


class TestSerialResumeHonoursTheBankedPrefix:
    """The serial path does what the hold surface promises."""

    @pytest.mark.asyncio
    async def test_serial_skips_banked_iterations(self):
        ran = await _run(_fanout(6, parallel=False), _ctx_with_banked(4))
        assert ran == [4, 5], (
            f"serial resume re-ran banked work: executed {ran}, expected [4, 5]"
        )


class TestParallelResumeHonoursTheBankedSet:
    """Fact 2, now closed.  These were the inverted gap assertions.

    They previously pinned the cost bug — a 6-wide fan-out with 4 banked
    iterations re-ran all 6 — and asked to be inverted when the dispatch
    branch learned to honour the banked work.  It has: ``_execute_repeat``
    builds its task list from the indices with NO banked artifact.

    Note the semantic difference from the serial path above.  Serial resume
    takes a PREFIX, because iteration N depends on N-1 through
    ``{{previous}}``.  Parallel resume takes a SET, because iterations
    receive ``previous=None`` and are independent — so "which are already
    banked" is the only question with an answer, and a gap in the middle is
    re-run rather than forcing everything after it to re-run too.
    """

    @pytest.mark.asyncio
    async def test_parallel_branch_skips_banked_iterations(self):
        ran = await _run(_fanout(6, parallel=True), _ctx_with_banked(4))
        assert ran == [4, 5], (
            f"executed {ran} — expected only the unbanked [4, 5]. "
            f"Re-running banked subagents is the cost regression this "
            f"test exists to catch."
        )

    @pytest.mark.asyncio
    async def test_a_gap_in_the_banked_set_is_re_executed(self):
        """Set semantics, not prefix: a hole is filled, not a tail.

        The distinguishing case.  A prefix implementation given {0,2,4}
        would run 1..5; set semantics runs exactly {1,3}.  A parallel
        fan-out's faults are not contiguous — one throttled subagent out of
        twenty is the common shape — so this is the behaviour that matters.
        """
        ctx = be.ExecutionContext(
            run_id="r-gap", project_id="p", project_root="/tmp",
        )
        ctx.resume_from_block_id = "fanout"
        ctx.resume_iteration_artifacts = {
            i: Artifact(summary=f"banked {i}", created_at=time.time())
            for i in (0, 2, 4)
        }
        ran = await _run(_fanout(5, parallel=True), ctx)
        assert ran == [1, 3], f"expected the gaps [1, 3]; executed {ran}"

    @pytest.mark.asyncio
    async def test_the_banked_set_is_scoped_to_the_resume_target(self):
        """A different loop's banked indices must not skip this loop's work.

        Guards the one way selective replay could silently under-run: a
        deck with several loops where the artifacts belong to another.
        """
        ctx = _ctx_with_banked(4)
        ctx.resume_from_block_id = "some-other-loop"
        ran = await _run(_fanout(6, parallel=True), ctx)
        assert ran == [0, 1, 2, 3, 4, 5], (
            f"banked indices leaked across loops; executed {ran}"
        )


class TestConcurrentIterationWritesDoNotLoseRecords:
    """Counterpart check: persistence under a parallel fan-out is safe.

    ``append_iteration_summary`` is an unguarded read-modify-write
    (get -> mutate -> _write_json, no lock), which looks like a lost-update
    hazard for N concurrent iterations.  It is not: every call in that
    chain is synchronous, so each append is atomic with respect to the
    event loop -- there is no await between the read and the write for
    another coroutine to interleave on.

    Pinned because the reasoning is non-obvious and a future refactor that
    makes any part of that path async would silently start dropping
    iteration records from exactly the wide fan-outs that need them.
    """

    def test_twenty_concurrent_appends_all_persist(self, tmp_path):
        from app.storage.task_runs import TaskRunStorage
        from app.models.task_run import (
            TaskRunCreate, TaskRunBlockState, IterationSummary,
        )
        st = TaskRunStorage(tmp_path)
        run = st.create(TaskRunCreate(card_id="c1"))
        st.set_block_state(run.id, TaskRunBlockState(
            block_id="fanout", block_type="repeat", status="running",
        ))

        n = 20

        async def one(i: int) -> None:
            await asyncio.sleep(0)  # yield, as a real iteration does
            st.append_iteration_summary(run.id, "fanout", IterationSummary(
                index=i, status="passed", duration_ms=1, tokens=0,
                has_artifact=False,
            ))

        async def main() -> None:
            await asyncio.gather(*[one(i) for i in range(n)])

        asyncio.run(main())
        got = st.get(run.id).block_states["fanout"].iteration_summaries
        assert sorted(s.index for s in got) == list(range(n)), (
            "iteration records were lost under concurrency; if this fails, "
            "some part of append_iteration_summary's call chain became "
            "async and the read-modify-write is no longer atomic"
        )
