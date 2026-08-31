"""Gate-cancelled siblings must be persisted, and marked cancelled.

Two defects this pins, both found while building the run-map surface:

1. The infra re-raise sat ABOVE the loop that materialises exceptional
   iterations into Artifacts, so a gate-cancelled fan-out persisted NO
   iteration records at all.  design/task-cards.md states "every failing
   iteration is always persisted"; that contract was silently false on
   exactly the path that most needs it, and the run map had nothing to
   draw for the event that stopped the run.

2. ``asyncio.CancelledError`` is NOT a subclass of ``Exception``, so a
   sibling the gate killed never reached ``execute_block``'s
   ``except Exception`` and was recorded as a generic failure.  That
   reports a fan-out as N-wide broken when only the faulting subset was,
   and blames the card for the environment's fault.

The run must still HOLD in both cases -- persisting records is not a
licence to swallow the fault.
"""

import asyncio
import time

import pytest
from unittest.mock import patch

from app.agents.task_executor import TaskInfraError
from app.models.task_card import Artifact, Block
import app.agents.block_executor as be


def _fanout(n: int, parallel: bool = True) -> Block:
    return Block(
        id="rep", block_type="repeat", name="fan",
        repeat_mode="count", repeat_count=n,
        repeat_parallel=parallel, repeat_propagate="none",
        body=[Block(id="t1", block_type="task", name="w", instructions="go")],
    )


def _ctx():
    return be.ExecutionContext(
        run_id="r-iter-rec", project_id="p1", project_root="/tmp",
    )


@pytest.fixture
def recorder(monkeypatch):
    """Capture every _record_iteration call."""
    seen = []

    async def _rec(block, ctx, index, artifact, item_key=None):
        seen.append({
            "index": index,
            "failed": artifact.failed,
            "summary": artifact.summary,
            # Roster identity, threaded since the completeness assertion
            # landed.  Captured rather than merely tolerated: the
            # gate-cancelled path synthesizes its own artifact, so it is
            # a place the key could be dropped while every for_each test
            # that exercises the ordinary path still passed.
            "item_key": item_key,
        })

    monkeypatch.setattr(be, "_record_iteration", _rec)
    return seen


@pytest.fixture
def emitted(monkeypatch):
    """Capture relay events so iteration_completed status is checkable."""
    events = []

    async def _emit(ctx, evt):
        events.append(evt)

    monkeypatch.setattr(be, "_emit", _emit)
    return events


def _auth_at_index_zero(slow_secs: float = 5.0):
    """Index 0 raises a session-level fault; the rest are slow."""
    async def _run(block, **kwargs):
        from app.context import get_task_iteration_context
        idx = (get_task_iteration_context() or {}).get("index")
        if idx == 0:
            raise TaskInfraError(
                "credentials rejected",
                infra_kind="authentication_error",
                block_id="t1",
            )
        await asyncio.sleep(slow_secs)
        return Artifact(summary=f"iteration {idx} ok", created_at=time.time())
    return _run


class TestEveryIterationIsPersisted:

    @pytest.mark.asyncio
    async def test_gate_cancelled_siblings_are_recorded(
        self, monkeypatch, recorder,
    ):
        """The regression: zero records persisted for the whole collapse."""
        monkeypatch.setattr(
            be, "execute_task_block", _auth_at_index_zero(),
        )
        with pytest.raises(TaskInfraError):
            await be.execute_block(_fanout(4), _ctx())
        assert len(recorder) == 4, (
            f"expected a record for every iteration, got {len(recorder)}: "
            f"{[r['index'] for r in recorder]}"
        )
        assert sorted(r["index"] for r in recorder) == [0, 1, 2, 3]

    @pytest.mark.asyncio
    async def test_run_still_holds_after_persisting(
        self, monkeypatch, recorder,
    ):
        """Persisting records must not swallow the fault."""
        monkeypatch.setattr(
            be, "execute_task_block", _auth_at_index_zero(),
        )
        with pytest.raises(TaskInfraError) as ei:
            await be.execute_block(_fanout(4), _ctx())
        assert ei.value.infra_kind == "authentication_error"


class TestCancelledIsNotFailed:

    @pytest.mark.asyncio
    async def test_cancelled_siblings_are_not_marked_failed(
        self, monkeypatch, recorder,
    ):
        monkeypatch.setattr(
            be, "execute_task_block", _auth_at_index_zero(),
        )
        with pytest.raises(TaskInfraError):
            await be.execute_block(_fanout(4), _ctx())
        by_idx = {r["index"]: r for r in recorder}
        assert by_idx[0]["failed"] is True, "the real fault must read failed"
        for i in (1, 2, 3):
            assert by_idx[i]["failed"] is False, (
                f"iteration {i} was cancelled by the harness, not a failure "
                f"of the work: {by_idx[i]['summary']!r}"
            )

    @pytest.mark.asyncio
    async def test_cancelled_summary_explains_why(
        self, monkeypatch, recorder,
    ):
        """A bare 'raised CancelledError' tells the reader nothing."""
        monkeypatch.setattr(
            be, "execute_task_block", _auth_at_index_zero(),
        )
        with pytest.raises(TaskInfraError):
            await be.execute_block(_fanout(3), _ctx())
        cancelled = [r for r in recorder if not r["failed"]]
        assert cancelled, "expected at least one cancelled sibling"
        for r in cancelled:
            assert "cancelled" in r["summary"].lower()
            assert "infrastructure" in r["summary"].lower()

    @pytest.mark.asyncio
    async def test_iteration_completed_event_carries_cancelled_status(
        self, monkeypatch, recorder, emitted,
    ):
        """The dot strip reads this event, so the status must be right."""
        monkeypatch.setattr(
            be, "execute_task_block", _auth_at_index_zero(),
        )
        with pytest.raises(TaskInfraError):
            await be.execute_block(_fanout(4), _ctx())
        completions = {
            e["index"]: e["status"] for e in emitted
            if e.get("type") == "iteration_completed"
        }
        assert completions.get(0) == "failed"
        for i in (1, 2, 3):
            assert completions.get(i) == "cancelled", (
                f"iteration {i} emitted {completions.get(i)!r}; the run map "
                f"would draw it as a work failure"
            )


class TestOrdinaryFailuresUnaffected:
    """The fix must not reclassify genuine failures as cancelled."""

    @pytest.mark.asyncio
    async def test_plain_exception_still_records_failed(
        self, monkeypatch, recorder,
    ):
        async def _fail(block, **kwargs):
            raise RuntimeError("the work broke on its merits")

        monkeypatch.setattr(be, "execute_task_block", _fail)
        art = await be.execute_block(_fanout(3), _ctx())
        assert art.failed
        assert len(recorder) == 3
        assert all(r["failed"] is True for r in recorder), (
            "a real failure must not be laundered into 'cancelled'"
        )
