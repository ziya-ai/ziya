"""The executor half: resume must WALK THROUGH a Call and skip banked work.

Companion to ``test_resume_through_call.py``, which covers resolution.
Resolution alone is not enough — it can name ``b-cf96c4e2`` inside a
called card all day, but the executor's resume gate walks the CALLER's
tree, and a Call block has an empty ``body``, so:

  * ``_subtree_contains(call_block, target)`` is False, and
  * ``_replay_artifact`` swallows the whole call.

``resume_skipping`` then never clears and the run reports success having
executed nothing, which is strictly worse than the 404 it replaces.

So two behaviours are asserted here, both against the real
``execute_block`` walk:

1. **Descent.** A resume target inside a callee is reached: blocks before
   it inside the callee replay, the target executes, blocks after it
   execute.
2. **Selective parallel replay.** A parallel fan-out at the resume point
   executes only iterations with no banked artifact.  This is the money
   test: the reported study lost one subagent of twenty to an expired
   credential, and re-running the other nineteen cost 14 hours.

Both fail against unpatched code, which is the point — a test that passes
without the fix would certify the bug.  The parallel one supersedes
``test_parallel_resume_replay.TestParallelResumeDoesNotHonourIt``, whose
docstring asks to be inverted when this lands.
"""

import time
from typing import Any, Dict, List

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
import app.agents.block_executor as be


# ---------------------------------------------------------------- fixtures

def _task(bid: str, name: str = "t") -> Block:
    return Block(id=bid, block_type="task", name=name, instructions="go")


def _callee(parallel: bool = True, width: int = 20) -> Block:
    """CL1: recon, a 20-wide auditor fan-out, then a merge."""
    return Block(
        id="b-cl1-root", block_type="group", name="CL1",
        body=[
            _task("b-recon", "Stage 1: Recon"),
            Block(
                id="b-cf96c4e2", block_type="repeat",
                name="Stage 2: Parallel subsystem auditors",
                repeat_mode="count", repeat_count=width,
                repeat_parallel=parallel, repeat_propagate="none",
                body=[_task("b-auditor", "Audit subsystem")],
            ),
            _task("b-merge", "Stage 3: Merge"),
        ],
    )


def _caller() -> Block:
    """CL0: a State block, then two Calls with EMPTY bodies."""
    return Block(
        id="root", block_type="group", name="CL0",
        body=[
            Block(id="b-params", block_type="state", name="Study parameters",
                  state_context="Target is staging."),
            Block(id="call-p1", block_type="call", name="Phase 1",
                  call_target="CL1", call_target_kind="card"),
            Block(id="call-p2", block_type="call", name="Phase 2",
                  call_target="CL2", call_target_kind="card"),
        ],
    )


def _resolver(callee: Block):
    """Stand in for task_call.resolve_call_target without storage."""
    from app.agents.task_call import ResolvedCall

    def _resolve(target, kind, *, project_id=None, project_root=None):
        if target == "CL1":
            return ResolvedCall(
                kind="card", key="card:cl1", label="CL1", root=callee,
            )
        return ResolvedCall(
            kind="card", key=f"card:{target}", label=target,
            root=Block(id=f"b-{target}-root", block_type="group",
                       name=target, body=[_task(f"b-{target}-work")]),
        )
    return _resolve


class _Trace:
    """Records what executed, what replayed, and at which loop indices."""

    def __init__(self) -> None:
        self.executed: List[str] = []
        self.iterations: List[int] = []
        self.replayed_events: List[Dict[str, Any]] = []

    async def task(self, blk, **kw) -> Artifact:
        from app.context import get_task_iteration_context
        self.executed.append(blk.id or blk.name or "?")
        ictx = get_task_iteration_context() or {}
        idx = ictx.get("index")
        if idx is not None:
            self.iterations.append(idx)
        return Artifact(summary=f"ran {blk.id}", created_at=time.time())


async def _run(
    root: Block, ctx, callee: Block, trace: _Trace,
) -> Artifact:
    async def _noop_record(*a, **k):
        return None

    real_emit = be._emit

    async def _emit(c, event):
        if event.get("replayed"):
            trace.replayed_events.append(event)
        return await real_emit(c, event)

    with patch.object(be, "execute_task_block", trace.task), \
         patch.object(be, "_record_iteration", _noop_record), \
         patch.object(be, "_emit", _emit), \
         patch("app.agents.task_call.resolve_call_target", _resolver(callee)):
        return await be.execute_block(root, ctx)


def _ctx(**kw):
    ctx = be.ExecutionContext(
        run_id="r-descend", project_id="p", project_root="/tmp",
    )
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


# ------------------------------------------------------------------- tests

class TestResumeDescendsIntoACallee:
    """Defect 1: a Call is replayed whole, so its interior is unreachable."""

    @pytest.mark.asyncio
    async def test_target_inside_the_callee_actually_executes(self):
        callee = _callee(parallel=False, width=2)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-merge",
            resume_skipping=True,
            resume_call_chain=["call-p1"],
            resume_artifacts={
                "b-recon": Artifact(summary="banked recon",
                                    created_at=time.time()),
            },
        )
        await _run(_caller(), ctx, callee, trace)

        assert "b-merge" in trace.executed, (
            f"the resume target inside the callee never ran (executed "
            f"{trace.executed}); the gate replayed the Call whole"
        )
        assert "b-recon" not in trace.executed, (
            "b-recon precedes the target inside the callee and must replay "
            "from record, not re-run"
        )
        assert ctx.resume_skipping is False, (
            "resume_skipping never cleared — the run would report success "
            "having executed nothing"
        )

    @pytest.mark.asyncio
    async def test_calls_before_the_target_replay_whole(self):
        """An EARLIER phase must still be replayed, not descended into.

        The descent must be surgical: only the Call named in
        ``resume_call_chain`` opens up.  A sibling Call that precedes it
        has to keep the old replay-whole behaviour, or "resume at phase 3"
        would silently re-run phases 1 and 2 as well — the same class of
        cost bug this fix exists to remove, just displaced.

        Note this asserts about a call BEFORE the target.  An earlier
        version of this test asserted that Phase 2 (which FOLLOWS the
        target) did not execute, which is exactly backwards: everything
        after the resume point must run, and
        ``test_phases_after_the_call_execute`` asserts precisely that.
        The two contradicted each other, and the mistake was mine, not
        the executor's.
        """
        caller = Block(
            id="root", block_type="group", name="CL0",
            body=[
                Block(id="call-p0", block_type="call", name="Phase 0",
                      call_target="CL0a", call_target_kind="card"),
                Block(id="call-p1", block_type="call", name="Phase 1",
                      call_target="CL1", call_target_kind="card"),
            ],
        )
        callee = _callee(parallel=False, width=1)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-merge", resume_skipping=True,
            resume_call_chain=["call-p1"],
        )
        await _run(caller, ctx, callee, trace)

        assert "b-CL0a-work" not in trace.executed, (
            f"Phase 0 precedes the resumed phase and must replay from "
            f"record; executed {trace.executed}"
        )
        assert "b-merge" in trace.executed, (
            f"the target inside Phase 1 still has to run; executed "
            f"{trace.executed}"
        )

    @pytest.mark.asyncio
    async def test_phases_after_the_call_execute(self):
        """Once the target is passed, later phases run for real."""
        callee = _callee(parallel=False, width=1)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-merge", resume_skipping=True,
            resume_call_chain=["call-p1"],
        )
        await _run(_caller(), ctx, callee, trace)
        assert "b-CL2-work" in trace.executed, (
            f"Phase 2 must execute after the resumed block; executed "
            f"{trace.executed}"
        )

    @pytest.mark.asyncio
    async def test_without_the_chain_the_call_is_replayed_whole(self):
        """Pins WHY the chain is passed in rather than derived.

        A Call's body is empty, so nothing in the tree reveals that the
        target is beneath it.  With no chain the old behaviour must persist
        exactly — this is the pre-fix state, kept as a contrast so the
        chain's necessity is documented rather than asserted in a comment.
        """
        callee = _callee(parallel=False, width=1)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-merge", resume_skipping=True,
            resume_call_chain=[],
        )
        await _run(_caller(), ctx, callee, trace)
        assert "b-merge" not in trace.executed
        assert ctx.resume_skipping is True


class TestSelectiveParallelReplay:
    """Defect 2: the parallel branch re-ran every banked iteration."""

    @pytest.mark.asyncio
    async def test_nineteen_banked_of_twenty_runs_only_one(self):
        """The reported case, exactly: 19 passed, 1 faulted, 20 wide."""
        callee = _callee(parallel=True, width=20)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-cf96c4e2", resume_skipping=True,
            resume_call_chain=["call-p1"],
            resume_artifacts={
                "b-recon": Artifact(summary="recon", created_at=time.time()),
            },
            resume_iteration_artifacts={
                i: Artifact(summary=f"audit {i}", created_at=time.time())
                for i in range(19)
            },
        )
        await _run(_caller(), ctx, callee, trace)

        assert sorted(trace.iterations) == [19], (
            f"executed iterations {sorted(trace.iterations)} — expected only "
            f"[19]. Re-running the 19 banked subagents is the 14-hour "
            f"regression this test exists to prevent."
        )

    @pytest.mark.asyncio
    async def test_banked_iterations_are_reported_as_replayed(self):
        """Their outputs must reach the loop result, not vanish."""
        callee = _callee(parallel=True, width=4)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-cf96c4e2", resume_skipping=True,
            resume_call_chain=["call-p1"],
            resume_iteration_artifacts={
                i: Artifact(summary=f"audit {i}", created_at=time.time())
                for i in range(3)
            },
        )
        await _run(_caller(), ctx, callee, trace)
        idxs = sorted(e["index"] for e in trace.replayed_events
                      if e.get("block_id") == "b-cf96c4e2")
        assert idxs == [0, 1, 2], (
            f"replayed iterations must still be announced so the dot strip "
            f"shows preserved work; got {idxs}"
        )

    @pytest.mark.asyncio
    async def test_a_failed_iteration_is_re_executed(self):
        """Only banked indices are skipped; gaps are the work to redo."""
        callee = _callee(parallel=True, width=5)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-cf96c4e2", resume_skipping=True,
            resume_call_chain=["call-p1"],
            resume_iteration_artifacts={
                0: Artifact(summary="a", created_at=time.time()),
                2: Artifact(summary="c", created_at=time.time()),
                4: Artifact(summary="e", created_at=time.time()),
            },
        )
        await _run(_caller(), ctx, callee, trace)
        assert sorted(trace.iterations) == [1, 3], (
            f"expected the unbanked gaps [1, 3]; got "
            f"{sorted(trace.iterations)}"
        )

    @pytest.mark.asyncio
    async def test_banked_set_is_ignored_for_a_different_loop(self):
        """Guards against one loop's indices skipping another's work.

        A deck can hold several loops; ``resume_iteration_artifacts`` is
        scoped to the resume target, so a loop that is NOT the target must
        run every iteration.
        """
        callee = _callee(parallel=True, width=3)
        trace = _Trace()
        ctx = _ctx(
            resume_from_block_id="b-merge", resume_skipping=True,
            resume_call_chain=["call-p1"],
            resume_iteration_artifacts={
                0: Artifact(summary="x", created_at=time.time()),
            },
        )
        await _run(_caller(), ctx, callee, trace)
        # The loop precedes the target, so it replays as a BLOCK and runs
        # no iterations at all — the banked set must not leak into it.
        assert trace.iterations == [], (
            f"a block before the resume target must replay wholesale; "
            f"iterations {trace.iterations} ran"
        )

    @pytest.mark.asyncio
    async def test_a_plain_launch_is_unaffected(self):
        """No resume state: every iteration runs, as before."""
        callee = _callee(parallel=True, width=4)
        trace = _Trace()
        await _run(_caller(), _ctx(), callee, trace)
        assert sorted(trace.iterations) == [0, 1, 2, 3]
