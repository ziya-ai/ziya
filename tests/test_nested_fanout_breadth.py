"""Breadth reporting must not exceed its own denominator.

``infra_faults`` is RUN-scoped (one list for the whole run) while
``fanout_width`` describes ONE loop.  Nested fan-outs break the implied
relationship between them: an outer 3-wide loop whose body is an inner
10-wide loop can accumulate ~30 faults against a denominator of 10, and
the surface renders "33 of 10 subagents — fleet-wide".

Observed, not theorised: driving nested parallel repeats through the real
executor produced exactly that.  ``infra_widest_fanout`` (a max) keeps the
denominator at the widest single loop, which is right for the common
single-fan-out case and wrong here, because the numerator is summed across
every loop the run entered.

The fix is a floor on the denominator rather than a cap on the numerator:
dropping faults would understate the damage, whereas widening the
denominator to at least the fault count states the honest thing ("33 of
33") and leaves ``fleet_wide`` meaningful.  A cap on the numerator would
also silently disagree with ``kinds``, whose counts are unclamped.

Nested fan-outs are not hypothetical for this codebase: a Repeat whose
body contains a Call to a card that itself fans out is the natural shape
of a multi-phase study, and that is the configuration these tests model.
"""

import asyncio

import pytest
from unittest.mock import patch

from app.agents.task_executor import TaskInfraError
from app.models.task_card import Block
from app.utils.infra_gate import InfraFault, gate_reason, summarize
import app.agents.block_executor as be


def _faults(n: int, kind: str = "throttling_error") -> list:
    return [
        InfraFault(kind=kind, block_id=f"b{i}", call_path=("CL0",), index=i)
        for i in range(n)
    ]


class TestSummaryNeverExceedsItsDenominator:

    def test_more_faults_than_width_is_not_reported_as_a_ratio_over_one(self):
        s = summarize(_faults(4), fanout_width=3)
        assert s["fault_count"] <= s["fanout_width"], (
            f"reported {s['fault_count']} of {s['fanout_width']} — a "
            f"surface rendering this prints '4 of 3 subagents'"
        )

    def test_the_widened_denominator_keeps_the_fault_count_intact(self):
        """Widen the denominator, never drop faults.

        Clamping the numerator would understate the damage AND disagree
        with `kinds`, whose counts are not clamped.
        """
        s = summarize(_faults(4), fanout_width=3)
        assert s["fault_count"] == 4
        assert sum(s["kinds"].values()) == 4

    def test_the_normal_case_is_unchanged(self):
        """Guard against 'fixing' this by always widening."""
        s = summarize(_faults(18), fanout_width=20)
        assert s["fanout_width"] == 20
        assert s["fault_count"] == 18

    def test_gate_reason_reports_no_percentage_above_one_hundred(self):
        reason = gate_reason(_faults(4), fanout_width=3)
        assert reason is not None
        assert "133%" not in reason, reason
        # Any 3-digit percentage is the same defect with different numbers.
        import re
        pcts = [int(m) for m in re.findall(r"(\d+)%", reason)]
        assert all(p <= 100 for p in pcts), f"{pcts} in {reason!r}"


class TestNestedFanOutThroughTheExecutor:
    """The path that actually produced the bad string."""

    @pytest.mark.asyncio
    async def test_nested_parallel_repeats_report_a_sane_ratio(self):
        async def _boom(block, **kw):
            raise TaskInfraError(
                "t", infra_kind="throttling_error",
                block_id=block.id or "?",
            )

        inner = Block(
            id="inner", block_type="repeat", name="inner",
            repeat_mode="count", repeat_count=10, repeat_parallel=True,
            repeat_propagate="none",
            body=[Block(id="t", block_type="task", name="a",
                        instructions="go")],
        )
        outer = Block(
            id="outer", block_type="repeat", name="outer",
            repeat_mode="count", repeat_count=3, repeat_parallel=True,
            repeat_propagate="none", body=[inner],
        )
        ctx = be.ExecutionContext(
            run_id="r", project_id="p", project_root="/tmp",
        )
        with patch.object(be, "execute_task_block", _boom), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            with pytest.raises(TaskInfraError):
                await be.execute_block(outer, ctx)

        s = ctx.infra_summary()
        assert s["fault_count"] <= s["fanout_width"], (
            f"nested fan-out reported {s['fault_count']} of "
            f"{s['fanout_width']}; the tile would print that verbatim"
        )

    @pytest.mark.asyncio
    async def test_a_single_fan_out_still_reports_its_real_width(self):
        """The 18-of-20 case must not be widened to 18-of-18.

        Without this, a fix that always sets width=max(width, count)
        would erase the distinction between a partial collapse and a
        total one — which is the whole point of `fleet_wide`.
        """
        async def _boom(block, **kw):
            from app.context import get_task_iteration_context
            idx = (get_task_iteration_context() or {}).get("index")
            if idx is not None and idx < 4:
                raise TaskInfraError(
                    "t", infra_kind="throttling_error", block_id="t")
            from app.models.task_card import Artifact
            import time as _t
            return Artifact(summary="ok", created_at=_t.time())

        blk = Block(
            id="fan", block_type="repeat", name="fan",
            repeat_mode="count", repeat_count=20, repeat_parallel=True,
            repeat_propagate="none",
            body=[Block(id="t", block_type="task", name="a",
                        instructions="go")],
        )
        ctx = be.ExecutionContext(
            run_id="r", project_id="p", project_root="/tmp",
        )
        with patch.object(be, "execute_task_block", _boom), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            with pytest.raises(TaskInfraError):
                await be.execute_block(blk, ctx)

        s = ctx.infra_summary()
        assert s["fanout_width"] == 20, (
            f"a genuine 20-wide fan-out reported width "
            f"{s['fanout_width']}; the real width must survive"
        )
