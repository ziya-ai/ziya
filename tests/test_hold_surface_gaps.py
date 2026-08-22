"""Known gaps in the infrastructure-hold surfaces.

Each test pins CURRENT behaviour that is wrong or incomplete, so the gap
is visible in CI rather than living in a conversation. Every one was
found by probing the running code, not by reading it.

The pattern across all of them is the same: the hold machinery was built
against the Repeat block's parallel fan-out (the shape that prompted it),
and the other container shapes and the labelling path were never wired
to it. Each is individually small; together they mean a hold outside a
parallel Repeat reports less than the one inside it.

Written as assert-current-behaviour rather than xfail because an xfail is
invisible in a green run, and the whole class of defect being tracked
here is "the feature could not possibly work while its unit tests were
green".
"""

import asyncio
import time

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.agents.task_executor import TaskInfraError
import app.agents.block_executor as be
from app.utils.infra_gate import InfraFault, summarize


def _infra(block_id: str = "t"):
    return TaskInfraError(
        "credentials rejected",
        infra_kind="authentication_error",
        block_id=block_id,
    )


class TestParallelBlockHasNoBreadthAndNoGate:
    """GAP 1: ``_execute_parallel`` re-raises but never records.

    ``_execute_repeat``'s ``_run_one`` calls ``ctx.record_infra_fault``
    in its except before propagating; the sibling container has no
    equivalent, so a Parallel block's hold reports ``fault_count: 0`` and
    ``fleet_wide: False`` no matter how many children died. On the run
    surface that renders as no breadth strip at all -- the reader is told
    a hold happened and given nothing to act on.

    It also has no gate: the watcher that cancels in-flight siblings
    lives only in the Repeat path, so every child runs to completion
    against the dead dependency first. Measured at 3.01 s for three 3 s
    children, i.e. the full sibling duration.

    Fix is two lines mirroring the Repeat path (record in an except,
    cancel from a concurrent watcher). Not applied here because it is a
    behaviour change to a second container, and the CL6 critique stage
    uses a Parallel block -- worth landing deliberately rather than as a
    drive-by.
    """

    @pytest.mark.asyncio
    async def test_parallel_block_records_no_faults(self):
        async def _work(block, **kw):
            if block.id == "a":
                raise _infra("a")
            return Artifact(summary="ok", created_at=time.time())

        blk = Block(id="par", block_type="parallel", name="critics", body=[
            Block(id="a", block_type="task", name="A", instructions="x"),
            Block(id="b", block_type="task", name="B", instructions="x"),
        ])
        ctx = be.ExecutionContext(
            run_id="r-par", project_id="p", project_root="/tmp",
        )
        with patch.object(be, "execute_task_block", _work):
            with pytest.raises(TaskInfraError):
                await be.execute_block(blk, ctx)

        assert ctx.infra_faults == [], (
            "if this is now non-empty, _execute_parallel has learned to "
            "record faults -- invert this test"
        )
        summary = ctx.infra_summary()
        assert summary["fault_count"] == 0
        assert summary["fleet_wide"] is False, (
            "a Parallel block's hold cannot report fleet-wide breadth, so "
            "the banner's FLEET badge never renders for this shape"
        )

    @pytest.mark.asyncio
    async def test_parallel_block_waits_for_every_sibling(self):
        """No gate: the hold surfaces only after the slowest child."""
        async def _work(block, **kw):
            if block.id == "a":
                raise _infra("a")
            await asyncio.sleep(0.5)
            return Artifact(summary="ok", created_at=time.time())

        blk = Block(id="par", block_type="parallel", name="critics", body=[
            Block(id="a", block_type="task", name="A", instructions="x"),
            Block(id="b", block_type="task", name="B", instructions="x"),
        ])
        ctx = be.ExecutionContext(
            run_id="r-par2", project_id="p", project_root="/tmp",
        )
        t0 = time.time()
        with patch.object(be, "execute_task_block", _work):
            with pytest.raises(TaskInfraError):
                await be.execute_block(blk, ctx)
        elapsed = time.time() - t0
        assert elapsed >= 0.4, (
            f"held after {elapsed:.2f}s -- if this is now near-zero, a gate "
            f"was added to _execute_parallel; invert this test"
        )


class TestUntilLoopHasNoGate:
    """GAP 2: the Until loop neither records faults nor gates.

    An Until loop is serial, so the cost is bounded by one iteration
    rather than by the fan-out width -- but a loop whose condition is
    'until the tests pass' will keep re-entering against dead
    infrastructure until ``until_max``, and its hold reports no breadth.

    The run still HOLDS correctly (the exception propagates through
    ``_execute_sequence``), so this is a cost-and-reporting gap, not a
    correctness one.
    """

    def test_until_has_no_gate_or_recording(self):
        import inspect
        src = inspect.getsource(be._execute_until)
        assert "infra_gate_closed" not in src, (
            "a gate appeared in _execute_until -- invert this test"
        )
        assert "record_infra_fault" not in src, (
            "fault recording appeared in _execute_until -- invert this test"
        )


class TestBreadcrumbShowsIdsNotNames:
    """GAP 3: ``call_path`` carries opaque keys, not card names.

    ``ExecutionContext.call_stack`` stores ``resolved.key``
    (``f"card:{card.id}"``) because its original jobs were cycle
    detection and depth limiting, where identity matters and legibility
    does not. The hold surfaces then reuse it as a human breadcrumb, so
    what reaches the UI is ``card:8f3a-...`` rather than
    ``CL1: Ziya Ground Truth``.

    ``ResolvedCall`` already carries ``label`` (the card's name), so the
    fix is to push a (key, label) pair -- but ``call_stack`` is also read
    by the cycle check, which compares against ``resolved.key``, so this
    cannot be a blind swap.
    """

    def test_call_path_is_not_human_readable(self):
        fault = InfraFault(
            kind="authentication_error", block_id="b1",
            call_path=("card:8f3a2b1c", "card:9d4e5f6a"), index=0,
        )
        path = summarize([fault], 20)["call_path"]
        assert all(p.startswith("card:") for p in path), (
            "call_path entries are no longer raw keys -- if labels are now "
            "carried, invert this test and check the cycle-detection "
            "comparison in _execute_call still uses the key"
        )


class TestSerialFanOutStillHolds:
    """Counterpart: confirm the serial gate does not swallow the hold.

    ``_execute_repeat``'s serial path ``break``s when the gate closes,
    which on its own would end the loop and let the block return a
    normal artifact -- reporting a run that stopped on dead
    infrastructure as complete. It does not, because the raising
    iteration propagates through ``_execute_sequence`` first, so the
    break is only reached on a LATER iteration.

    Pinned because the two mechanisms are in different functions and the
    safety depends on their ordering, which nothing else asserts.
    """

    @pytest.mark.asyncio
    async def test_serial_gate_does_not_mask_the_hold(self):
        async def _work(block, **kw):
            from app.context import get_task_iteration_context
            idx = (get_task_iteration_context() or {}).get("index")
            if idx == 1:
                raise _infra("t")
            return Artifact(summary=f"ok {idx}", created_at=time.time())

        blk = Block(
            id="rep", block_type="repeat", name="serial",
            repeat_mode="count", repeat_count=6, repeat_parallel=False,
            repeat_propagate="none", on_failure="continue",
            body=[Block(id="t", block_type="task", name="a",
                        instructions="go")],
        )
        ctx = be.ExecutionContext(
            run_id="r-serial", project_id="p", project_root="/tmp",
        )

        async def _noop(*a, **k):
            return None

        with patch.object(be, "execute_task_block", _work), \
             patch.object(be, "_record_iteration", _noop):
            with pytest.raises(TaskInfraError) as ei:
                await be.execute_block(blk, ctx)
        assert ei.value.infra_kind == "authentication_error"
        assert len(ctx.infra_faults) == 1
