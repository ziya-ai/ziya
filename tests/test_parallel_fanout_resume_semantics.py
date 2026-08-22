"""Pin the ACTUAL resume semantics of a parallel fan-out.

Written because a UX surface was built on a claim about this that turned
out to be false: that resuming a held fan-out would re-run only the
subagent that faulted.  It does not, and cannot, for two independent
reasons that this file records so the next person to design a surface on
top of resume reads the real contract rather than inferring one.

1. Iteration-level resume is REFUSED outright for a parallel loop
   (``resolve_iteration_resume``).  The refusal is correct: parallel
   iterations cannot see each other (``_execute_repeat`` gives them
   ``previous=None``), so "resume at index N" has no ordering meaning --
   0..N-1 were never prerequisites of N.  Honouring it would run fewer
   iterations than the card asks for while reporting the loop complete.

2. Block-level resume therefore re-runs the WHOLE loop.  ``resume_at``
   in ``_execute_repeat`` is gated on ``resume_from_iteration`` being
   set, which the block-level path never sets -- so every iteration
   executes again, including the ones that already succeeded.

The user-facing consequence is the thing worth pinning: retrying a held
20-wide fan-out where 18 faulted on a dead credential and 2 had already
succeeded re-runs all 20.  That is *defensible* (the 2 successes were
cheap relative to a wrong answer) but it is not free, and a surface that
implies otherwise is lying about cost -- which on a frontier-tier
20-agent fan-out is real money.

These tests assert current behaviour, NOT desired behaviour.  If
selective re-execution of failed parallel iterations is ever implemented,
they should fail loudly and be rewritten -- that is their job.
"""

import asyncio
import time

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.utils.resume_targets import resolve_iteration_resume
import app.agents.block_executor as be


def _fanout(parallel: bool, width: int = 20) -> dict:
    """A raw (dict) fan-out loop, as a card_snapshot stores it."""
    return {
        "id": "fanout", "block_type": "repeat", "name": "Auditors",
        "repeat_mode": "count", "repeat_count": width,
        "repeat_parallel": parallel, "body": [
            {"id": "t", "block_type": "task", "name": "a",
             "instructions": "go", "body": []},
        ],
    }


def _root(loop: dict) -> dict:
    return {"id": "root", "block_type": "group", "name": "c",
            "body": [loop], }


def _summaries(width: int, failed_at: int) -> list:
    return [
        {"index": i, "status": "failed" if i == failed_at else "passed",
         "has_artifact": True}
        for i in range(width)
    ]


class TestIterationResumeIsRefusedForParallelLoops:
    """The refusal a UX surface must gate on BEFORE offering the control."""

    def test_retrying_one_faulted_subagent_is_refused(self):
        start, err = resolve_iteration_resume(
            _root(_fanout(True)), "fanout", 9,
            _summaries(20, failed_at=9), "retry_iteration",
        )
        assert start is None
        assert err is not None
        assert "parallel" in err.lower()

    def test_the_refusal_names_the_alternative(self):
        """A 422 that does not say what to do instead is a dead end.

        The alternative it names changed once selective replay landed: a
        block-level retry of a parallel loop now replays every banked
        iteration and re-runs only the unfinished ones, so the refusal must
        NOT say "retry the whole loop" — that wording described a cost the
        user no longer pays and would push them toward Restart instead.
        """
        _, err = resolve_iteration_resume(
            _root(_fanout(True)), "fanout", 9,
            _summaries(20, failed_at=9), "retry_iteration",
        )
        low = err.lower()
        assert "retry the loop" in low
        assert "replayed from record" in low
        assert "whole loop" not in low, (
            "the refusal still advertises a full re-run; selective replay "
            "makes that both wrong and discouraging"
        )

    def test_continue_iteration_is_refused_too(self):
        """Both modes, not just retry — the reason is the loop's shape."""
        start, err = resolve_iteration_resume(
            _root(_fanout(True)), "fanout", 9,
            _summaries(20, failed_at=9), "continue_iteration",
        )
        assert start is None
        assert "parallel" in err.lower()

    def test_the_same_loop_serial_is_accepted(self):
        """Proves the refusal is about parallelism, not about the fixture.

        Without this the test above would pass against a function that
        refused every resume for any reason.
        """
        start, err = resolve_iteration_resume(
            _root(_fanout(False)), "fanout", 9,
            _summaries(20, failed_at=9), "retry_iteration",
        )
        assert err is None
        assert start == 9


class TestBlockLevelResumeRerunsTheWholeFanout:
    """The cost the surface must not hide."""

    @pytest.mark.asyncio
    async def test_every_iteration_re_executes(self):
        ran: list = []

        async def _work(block, **kw):
            from app.context import get_task_iteration_context
            ran.append((get_task_iteration_context() or {}).get("index"))
            return Artifact(summary="ok", created_at=time.time())

        blk = Block(
            id="fanout", block_type="repeat", name="Auditors",
            repeat_mode="count", repeat_count=20, repeat_parallel=True,
            repeat_propagate="none",
            body=[Block(id="t", block_type="task", name="a",
                        instructions="go")],
        )
        # A resume carrying NO banked artifacts.  This is no longer what
        # the recovery banner's Retry performs — the endpoint now banks
        # every passed iteration holding a retained artifact — so this
        # covers the residual case instead: a loop whose iterations all
        # failed, or whose passes fell past the 50-pass retention cap, has
        # nothing to replay and must still run in full.  The assertion is
        # unchanged because that behaviour is unchanged.
        ctx = be.ExecutionContext(
            run_id="r", project_id="p", project_root="/tmp",
            resume_from_block_id="fanout",
            resume_from_iteration=None,
        )
        with patch.object(be, "execute_task_block", _work), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            await be.execute_block(blk, ctx)

        assert len(ran) == 20, (
            f"block-level resume re-executed {len(ran)} of 20 iterations; "
            f"if this is now fewer, selective replay was implemented and "
            f"this test plus the UX copy that depends on it must be updated"
        )

    @pytest.mark.asyncio
    async def test_iteration_level_resume_does_replay_a_prefix(self):
        """Contrast: the mechanism EXISTS, it just is not reachable here.

        A serial loop resumed mid-way replays its prefix, which is why the
        parallel case reads as a gap rather than as an absent feature.
        """
        ran: list = []

        async def _work(block, **kw):
            from app.context import get_task_iteration_context
            ran.append((get_task_iteration_context() or {}).get("index"))
            return Artifact(summary="ok", created_at=time.time())

        blk = Block(
            id="fanout", block_type="repeat", name="Serial",
            repeat_mode="count", repeat_count=10, repeat_parallel=False,
            repeat_propagate="last",
            body=[Block(id="t", block_type="task", name="a",
                        instructions="go")],
        )
        ctx = be.ExecutionContext(
            run_id="r", project_id="p", project_root="/tmp",
            resume_from_block_id="fanout",
            resume_from_iteration=7,
            resume_iteration_artifacts={
                i: Artifact(summary=f"prior {i}", created_at=time.time())
                for i in range(7)
            },
        )
        with patch.object(be, "execute_task_block", _work), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            await be.execute_block(blk, ctx)

        assert len(ran) == 3, (
            f"expected iterations 7,8,9 to execute and 0-6 to replay; "
            f"got {len(ran)} executions"
        )
