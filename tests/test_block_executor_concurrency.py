"""Concurrency bounding for parallel Task Card containers.

A parallel Repeat and a Parallel block both fan model-invoking work out
to one provider under one account rate limit.  Unbounded, a for_each
over a 60-item planner roster opened 60 concurrent streams; the provider
throttled, and since each iteration is its own asyncio Task the
rate-limit response arrived as 60 independent task failures instead of
one queue that needed slowing down.

These tests measure OBSERVED PEAK concurrency rather than asserting the
cap field is set, because the failure mode is behavioural: a gate
acquired in the wrong place (around create_task instead of inside the
coroutine) still leaves the field populated and the tests passing while
cancellation silently regresses.
"""

import asyncio

import pytest
from app.agents import block_executor as bx
from app.agents.block_executor import (
    BlockExecutionCancelled,
    DEFAULT_REPEAT_CONCURRENCY,
    ExecutionContext,
    _resolve_concurrency,
    execute_block,
)
from app.models.task_card import Artifact, Block


@pytest.fixture
def concurrency_probe(monkeypatch):
    """Record peak simultaneous in-flight task executions."""
    state = {"active": 0, "peak": 0, "count": 0}

    async def _fake(block, **kwargs):
        state["active"] += 1
        state["count"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            # Yield enough times that every dispatched task has a chance
            # to reach this point before any of them finishes; a single
            # sleep(0) would serialise and hide an unbounded fan-out.
            for _ in range(5):
                await asyncio.sleep(0)
        finally:
            state["active"] -= 1
        return Artifact(summary=block.name or "", created_at=0.0)

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    return state


def _ctx():
    return ExecutionContext(run_id="")


def _repeat(n, *, parallel=True, limit=None):
    return Block(
        block_type="repeat", id="r", repeat_mode="count", repeat_count=n,
        repeat_parallel=parallel, repeat_max_concurrency=limit,
        body=[Block(block_type="task", id="t", name="w", instructions="go")],
    )


# ── limit resolution ──────────────────────────────────────────

class TestResolveConcurrency:
    def test_block_value_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("ZIYA_TASK_MAX_CONCURRENCY", "3")
        assert _resolve_concurrency(5, planned=100) == 5

    def test_env_used_when_block_unset(self, monkeypatch):
        monkeypatch.setenv("ZIYA_TASK_MAX_CONCURRENCY", "3")
        assert _resolve_concurrency(None, planned=100) == 3

    def test_default_when_nothing_set(self, monkeypatch):
        monkeypatch.delenv("ZIYA_TASK_MAX_CONCURRENCY", raising=False)
        assert _resolve_concurrency(None, planned=100) == DEFAULT_REPEAT_CONCURRENCY

    def test_zero_means_unbounded(self, monkeypatch):
        monkeypatch.delenv("ZIYA_TASK_MAX_CONCURRENCY", raising=False)
        assert _resolve_concurrency(0, planned=100) == 0

    def test_negative_clamps_to_unbounded_not_negative(self, monkeypatch):
        monkeypatch.delenv("ZIYA_TASK_MAX_CONCURRENCY", raising=False)
        assert _resolve_concurrency(-4, planned=100) == 0

    def test_unparseable_env_is_ignored_not_fatal(self, monkeypatch):
        monkeypatch.setenv("ZIYA_TASK_MAX_CONCURRENCY", "eight")
        assert _resolve_concurrency(None, planned=100) == DEFAULT_REPEAT_CONCURRENCY


# ── observed behaviour ────────────────────────────────────────

async def test_parallel_repeat_respects_default_cap(
    concurrency_probe, monkeypatch,
):
    """The regression this feature exists for: 60 planned, 8 in flight."""
    monkeypatch.delenv("ZIYA_TASK_MAX_CONCURRENCY", raising=False)
    await execute_block(_repeat(60), _ctx())
    assert concurrency_probe["count"] == 60, "every iteration must still run"
    assert concurrency_probe["peak"] <= DEFAULT_REPEAT_CONCURRENCY


async def test_parallel_repeat_respects_explicit_block_limit(
    concurrency_probe,
):
    await execute_block(_repeat(20, limit=3), _ctx())
    assert concurrency_probe["count"] == 20
    assert concurrency_probe["peak"] <= 3


async def test_explicit_zero_opts_out_of_bounding(concurrency_probe):
    """Opt-out must genuinely lift the cap, not silently clamp to default.

    Asserts peak ABOVE the default, so a gate that ignored the 0 and
    applied DEFAULT_REPEAT_CONCURRENCY would fail here.
    """
    await execute_block(_repeat(20, limit=0), _ctx())
    assert concurrency_probe["peak"] > DEFAULT_REPEAT_CONCURRENCY


async def test_small_fanout_is_not_throttled(concurrency_probe, monkeypatch):
    """Below the cap, all iterations should still overlap freely."""
    monkeypatch.delenv("ZIYA_TASK_MAX_CONCURRENCY", raising=False)
    await execute_block(_repeat(4), _ctx())
    assert concurrency_probe["peak"] == 4


async def test_serial_repeat_never_overlaps(concurrency_probe):
    """A non-parallel Repeat is unaffected by any of this."""
    await execute_block(_repeat(6, parallel=False), _ctx())
    assert concurrency_probe["count"] == 6
    assert concurrency_probe["peak"] == 1


async def test_parallel_block_children_are_bounded(
    concurrency_probe, monkeypatch,
):
    """A Parallel block shares the provider limit and so shares the cap."""
    monkeypatch.setenv("ZIYA_TASK_MAX_CONCURRENCY", "2")
    par = Block(
        block_type="parallel", id="p",
        body=[
            Block(block_type="task", id=f"t{i}", name=f"w{i}",
                  instructions="go")
            for i in range(8)
        ],
    )
    await execute_block(par, _ctx())
    assert concurrency_probe["count"] == 8
    assert concurrency_probe["peak"] <= 2


async def test_cancellation_still_works_under_the_gate(monkeypatch):
    """Queued iterations must remain cancellable.

    This is the test that catches gating dispatch instead of execution:
    if the gate wrapped create_task, iterations beyond the cap would have
    no Task for the watcher to cancel and this would hang or overrun.
    """
    monkeypatch.delenv("ZIYA_TASK_MAX_CONCURRENCY", raising=False)
    started = {"n": 0}

    async def _fake(block, **kwargs):
        started["n"] += 1
        await asyncio.sleep(0.05)
        return Artifact(summary="ok", created_at=0.0)

    monkeypatch.setattr(bx, "execute_task_block", _fake)

    cancel = {"flag": False}
    ctx = ExecutionContext(run_id="")
    monkeypatch.setattr(
        type(ctx), "cancel_requested",
        lambda self: cancel["flag"], raising=False,
    )

    async def _trip():
        await asyncio.sleep(0.06)
        cancel["flag"] = True

    # A cancelled parallel Repeat raises BlockExecutionCancelled, exactly
    # as the serial path does — the three top-level run sites
    # (task_cards API, scheduler, cli_card_runner) all catch it to mark
    # the run cancelled.  Swallowing it here would assert the opposite of
    # the real contract.
    async def _run_and_expect_cancel():
        with pytest.raises(BlockExecutionCancelled):
            await execute_block(_repeat(40), ctx)

    await asyncio.gather(_run_and_expect_cancel(), _trip())
    # Cancelled partway: far fewer than all 40 should ever have begun.
    assert started["n"] < 40
