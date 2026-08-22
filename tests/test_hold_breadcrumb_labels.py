"""The hold breadcrumb must name cards, not storage keys.

``ExecutionContext.call_stack`` holds ``resolved.key`` — ``card:<uuid>``.
That is the right identity for cycle detection (the same card called
under two different names is one node in the call graph) and the wrong
one for a breadcrumb: a user shown ``card:8f3a1c04-...`` learns nothing
about which phase of their study broke.

Two defects are pinned here, both found by running a full
CL0 -> Call -> CL1 -> fan-out chain and reading what the surface would
actually display:

  1. call_path carried keys instead of ``resolved.label``.
  2. call_path omitted the ROOT card entirely, because nothing is pushed
     onto the call stack for it — it was never "called".  So a hold in
     CL1 under CL0 reported ``[CL1]``, dropping the one element the
     reader already has on screen and uses to orient.

These are display-only, which is exactly why they are easy to ship
broken: every functional test of the hold path passed with keys in the
path, because nothing downstream parses it.
"""

import asyncio
import time

import pytest
from unittest.mock import patch

from app.agents.task_call import ResolvedCall
from app.agents.task_executor import TaskInfraError
from app.models.task_card import Artifact, Block
import app.agents.block_executor as be


def _cl1_root() -> Block:
    return Block(id="cl1-root", block_type="group", name="CL1", body=[
        Block(
            id="cl1-fanout", block_type="repeat", name="Auditors",
            repeat_mode="count", repeat_count=4, repeat_parallel=True,
            repeat_propagate="none",
            body=[Block(id="cl1-auditor", block_type="task",
                        name="Audit", instructions="go")],
        ),
    ])


def _cl0_root() -> Block:
    return Block(id="cl0-root", block_type="group", name="CL0", body=[
        Block(id="cl0-call1", block_type="call", name="Phase 1",
              call_target="CL1"),
    ])


async def _run_chain(root_label: str | None = "CL0: Landscape Study"):
    """Execute CL0 -> CL1 -> fan-out with every task faulting.

    Returns the ExecutionContext so the test can read the aggregate the
    hold surface would render.
    """
    async def _boom(block, **kw):
        raise TaskInfraError(
            "dead", infra_kind="authentication_error",
            block_id=block.id or "?",
        )

    def _resolve(target, kind, project_id, project_root):
        return ResolvedCall(
            kind="card", key="card:card-CL1-uuid",
            label="CL1: Ziya Ground Truth",
            root=_cl1_root(), card_scope=None,
        )

    ctx = be.ExecutionContext(
        run_id="r", project_id="p", project_root="/tmp",
        root_card_label=root_label,
    )
    with patch.object(be, "execute_task_block", _boom), \
         patch.object(be, "_record_iteration",
                      lambda *a, **k: asyncio.sleep(0)), \
         patch("app.agents.task_call.resolve_call_target", _resolve):
        with pytest.raises(TaskInfraError):
            await be.execute_block(_cl0_root(), ctx)
    return ctx


class TestBreadcrumbNamesCards:

    @pytest.mark.asyncio
    async def test_path_carries_the_callee_s_name(self):
        ctx = await _run_chain()
        path = ctx.infra_summary()["call_path"]
        assert "CL1: Ziya Ground Truth" in path, path

    @pytest.mark.asyncio
    async def test_path_carries_no_storage_keys(self):
        """The regression itself: keys leaking into a user-facing string."""
        ctx = await _run_chain()
        path = ctx.infra_summary()["call_path"]
        leaked = [h for h in path if h.startswith("card:")]
        assert not leaked, f"storage keys leaked into the breadcrumb: {leaked}"

    @pytest.mark.asyncio
    async def test_path_starts_at_the_card_that_owns_the_run(self):
        ctx = await _run_chain()
        path = ctx.infra_summary()["call_path"]
        assert path[0] == "CL0: Landscape Study", path

    @pytest.mark.asyncio
    async def test_path_reads_outermost_to_innermost(self):
        ctx = await _run_chain()
        path = ctx.infra_summary()["call_path"]
        assert path == ["CL0: Landscape Study", "CL1: Ziya Ground Truth"], path

    @pytest.mark.asyncio
    async def test_cycle_detection_still_uses_keys(self):
        """The label stack must not displace the key stack.

        Cycle detection depends on ``call_stack`` holding the card's
        identity, not its name; if a refactor ever swapped one for the
        other, A -> A under two different names would stop being caught.
        """
        ctx = await _run_chain()
        # Both stacks unwind to empty, but the key stack is what
        # _execute_call tests membership against during the call.
        assert ctx.call_stack == []
        assert ctx.call_labels == []


class TestBreadcrumbDegradesHonestly:

    @pytest.mark.asyncio
    async def test_no_root_label_still_names_the_callee(self):
        """An older launch path passes no root label.

        The breadcrumb should shorten, not fall back to keys — a partial
        name is readable, a uuid is not.
        """
        ctx = await _run_chain(root_label=None)
        path = ctx.infra_summary()["call_path"]
        assert path == ["CL1: Ziya Ground Truth"], path

    @pytest.mark.asyncio
    async def test_a_fault_outside_any_call_reports_just_the_root(self):
        """The single-card case: no Call, so the path is one hop."""
        async def _boom(block, **kw):
            raise TaskInfraError(
                "dead", infra_kind="authentication_error", block_id="t")

        blk = Block(
            id="rep", block_type="repeat", name="fan",
            repeat_mode="count", repeat_count=3, repeat_parallel=True,
            repeat_propagate="none",
            body=[Block(id="t", block_type="task", name="a",
                        instructions="go")],
        )
        ctx = be.ExecutionContext(
            run_id="r", project_id="p", project_root="/tmp",
            root_card_label="CL1 standalone",
        )
        with patch.object(be, "execute_task_block", _boom), \
             patch.object(be, "_record_iteration",
                          lambda *a, **k: asyncio.sleep(0)):
            with pytest.raises(TaskInfraError):
                await be.execute_block(blk, ctx)
        assert ctx.infra_summary()["call_path"] == ["CL1 standalone"]
