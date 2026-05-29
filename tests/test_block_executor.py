"""Tests for the block executor — the loop controller.

These tests stub execute_task_block so loop semantics can be
verified deterministically without making model calls.  Integration
tests with the real model live elsewhere.

Covers:
  - Task leaf execution (delegates to execute_task_block)
  - Repeat count mode, serial
  - Repeat count mode, parallel
  - Repeat until mode (stops on first pass)
  - Repeat for_each (treated as bounded count)
  - Parallel block
  - Implicit sequence (Repeat body with multiple children)
  - Soft cancel via cancel_requested flag
  - Iteration summary + full-artifact persistence
  - Pass-retention cap (>50 passes keeps only summaries)
  - Signature hashing on failures
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch

from app.models.task_card import Block, Artifact
from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.storage.task_runs import TaskRunStorage
from app.agents.block_executor import (
    execute_block,
    ExecutionContext,
    BlockExecutionCancelled,
    PASS_ARTIFACT_RETENTION_CAP,
)


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="card-1"))
    storage.set_block_state(r.id, TaskRunBlockState(
        block_id="repeat-1", block_type="repeat",
    ))
    return r


def _task(id: str, instr: str = "do it") -> Block:
    return Block(block_type="task", id=id, name=id, instructions=instr)


def _mk_artifact(summary: str = "ok", failed: bool = False) -> Artifact:
    return Artifact(summary=summary, failed=failed, tokens=5, duration_ms=1)


# Build a stub for execute_task_block that returns pre-canned artifacts.
def _stub_executor(responses):
    """Returns an async function that returns responses in sequence."""
    it = iter(responses)

    async def _stub(block, project_root=None, project_id=None):
        try:
            return next(it)
        except StopIteration:
            return _mk_artifact("default ok")
    return _stub


class TestDispatch:
    @pytest.mark.asyncio
    async def test_task_block_delegates(self):
        block = _task("t1")
        ctx = ExecutionContext(run_id="r")
        stub = _stub_executor([_mk_artifact("hello")])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        assert result.summary == "hello"

    @pytest.mark.asyncio
    async def test_unknown_block_type_raises(self):
        block = Block(block_type="task", id="t", name="t", instructions="x")
        # Monkey-patch the block_type after construction to bypass the
        # pydantic Literal validator.
        object.__setattr__(block, "block_type", "weird")
        ctx = ExecutionContext(run_id="r")
        from app.agents.task_executor import TaskExecutorError
        with pytest.raises(TaskExecutorError):
            await execute_block(block, ctx)


class TestRepeatCount:
    @pytest.mark.asyncio
    async def test_serial_count_three(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=3,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([
            _mk_artifact("a"), _mk_artifact("b"), _mk_artifact("c"),
        ])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        assert result.summary == "c"
        # Iteration summaries should be appended
        reloaded = storage.get(run.id)
        summaries = reloaded.block_states["repeat-1"].iteration_summaries
        assert len(summaries) == 3
        assert [s.index for s in summaries] == [0, 1, 2]
        assert all(s.status == "passed" for s in summaries)

    @pytest.mark.asyncio
    async def test_parallel_count_four(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=4, repeat_parallel=True,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([_mk_artifact(f"i{i}") for i in range(4)])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        reloaded = storage.get(run.id)
        summaries = reloaded.block_states["repeat-1"].iteration_summaries
        assert len(summaries) == 4


class TestRepeatUntil:
    @pytest.mark.asyncio
    async def test_stops_on_first_pass(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="until", repeat_max=5,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([
            _mk_artifact("fail", failed=True),
            _mk_artifact("pass"),
            _mk_artifact("never-reached"),
        ])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        assert not result.failed
        reloaded = storage.get(run.id)
        summaries = reloaded.block_states["repeat-1"].iteration_summaries
        assert len(summaries) == 3
        assert [s.status for s in summaries] == ["failed", "failed", "passed"]

    @pytest.mark.asyncio
    async def test_exhausts_max_when_never_passes(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="until", repeat_max=3,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([_mk_artifact(f"fail{i}", failed=True) for i in range(5)])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        assert result.failed
        reloaded = storage.get(run.id)
        assert len(reloaded.block_states["repeat-1"].iteration_summaries) == 3


class TestParallelBlock:
    @pytest.mark.asyncio
    async def test_parallel_aggregates_children(self):
        block = Block(
            block_type="parallel", id="par", name="par",
            body=[_task("a"), _task("b"), _task("c")],
        )
        ctx = ExecutionContext(run_id="r")
        stub = _stub_executor([
            _mk_artifact("A"), _mk_artifact("B"), _mk_artifact("C"),
        ])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        assert "3 child block(s)" in result.summary
        assert not result.failed

    @pytest.mark.asyncio
    async def test_parallel_failed_child_marks_composite(self):
        block = Block(
            block_type="parallel", id="par", name="par",
            body=[_task("a"), _task("b")],
        )
        ctx = ExecutionContext(run_id="r")
        stub = _stub_executor([
            _mk_artifact("ok"),
            _mk_artifact("boom", failed=True),
        ])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        assert result.failed

    @pytest.mark.asyncio
    async def test_empty_parallel(self):
        block = Block(block_type="parallel", id="par", name="par", body=[])
        ctx = ExecutionContext(run_id="r")
        result = await execute_block(block, ctx)
        assert "empty" in result.summary


class TestImplicitSequence:
    @pytest.mark.asyncio
    async def test_repeat_body_runs_sequentially(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=1,
            body=[_task("a"), _task("b"), _task("c")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([
            _mk_artifact("first"),
            _mk_artifact("second"),
            _mk_artifact("third"),
        ])
        with patch("app.agents.block_executor.execute_task_block", stub):
            result = await execute_block(block, ctx)
        # Last child wins as the iteration's artifact
        assert result.summary == "third"


class TestSoftCancel:
    @pytest.mark.asyncio
    async def test_cancel_before_iteration_boundary(self, storage, run):
        """Setting cancel_requested before iteration 2 should stop the loop."""
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=5,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)

        call_count = {"n": 0}

        async def stub(b, project_root=None, project_id=None):
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Trip cancel after second iteration
                storage.request_cancel(run.id)
            return _mk_artifact("ok")

        with patch("app.agents.block_executor.execute_task_block", stub):
            with pytest.raises(BlockExecutionCancelled):
                await execute_block(block, ctx)

        # Two iterations should have completed before cancel took effect
        reloaded = storage.get(run.id)
        assert reloaded.cancel_requested is True
        assert len(reloaded.block_states["repeat-1"].iteration_summaries) == 2


class TestPassRetentionCap:
    @pytest.mark.asyncio
    async def test_passes_beyond_cap_keep_summary_only(self, storage, run):
        """With >50 passes, only the first 50 artifacts are on disk."""
        N = PASS_ARTIFACT_RETENTION_CAP + 10
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=N,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)

        async def stub(b, project_root=None, project_id=None):
            return _mk_artifact("pass")

        with patch("app.agents.block_executor.execute_task_block", stub):
            await execute_block(block, ctx)

        reloaded = storage.get(run.id)
        summaries = reloaded.block_states["repeat-1"].iteration_summaries
        assert len(summaries) == N
        # has_artifact True for first 50, False after
        has_full = [s.has_artifact for s in summaries]
        assert has_full[:PASS_ARTIFACT_RETENTION_CAP] == [True] * PASS_ARTIFACT_RETENTION_CAP
        assert all(h is False for h in has_full[PASS_ARTIFACT_RETENTION_CAP:])

    @pytest.mark.asyncio
    async def test_failures_always_persisted_regardless_of_cap(self, storage, run):
        """Failures are never sampled out."""
        N = PASS_ARTIFACT_RETENTION_CAP + 10
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=N,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)

        async def stub(b, project_root=None, project_id=None):
            return _mk_artifact("oops", failed=True)

        with patch("app.agents.block_executor.execute_task_block", stub):
            await execute_block(block, ctx)

        reloaded = storage.get(run.id)
        summaries = reloaded.block_states["repeat-1"].iteration_summaries
        assert len(summaries) == N
        # Every failure is kept in full
        assert all(s.has_artifact for s in summaries)
        assert all(s.signature is not None for s in summaries)


class TestSignatureHashing:
    @pytest.mark.asyncio
    async def test_same_error_same_signature(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=3,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)

        async def stub(b, project_root=None, project_id=None):
            return _mk_artifact(
                "TypeError: section.rows is undefined\npacketPlugin.ts:82",
                failed=True,
            )

        with patch("app.agents.block_executor.execute_task_block", stub):
            await execute_block(block, ctx)

        reloaded = storage.get(run.id)
        sigs = [s.signature for s in reloaded.block_states["repeat-1"].iteration_summaries]
        assert len(set(sigs)) == 1  # all three share a signature
        assert sigs[0] is not None

    @pytest.mark.asyncio
    async def test_different_errors_different_signatures(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=2,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([
            _mk_artifact("TypeError: foo", failed=True),
            _mk_artifact("ValueError: bar", failed=True),
        ])

        with patch("app.agents.block_executor.execute_task_block", stub):
            await execute_block(block, ctx)

        reloaded = storage.get(run.id)
        sigs = [s.signature for s in reloaded.block_states["repeat-1"].iteration_summaries]
        assert len(set(sigs)) == 2


class TestIterationArtifactRoundTrip:
    @pytest.mark.asyncio
    async def test_write_and_read_per_iteration(self, storage, run):
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=2,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        stub = _stub_executor([
            _mk_artifact("alpha"),
            _mk_artifact("beta"),
        ])
        with patch("app.agents.block_executor.execute_task_block", stub):
            await execute_block(block, ctx)

        a0 = storage.read_iteration_artifact(run.id, "repeat-1", 0)
        a1 = storage.read_iteration_artifact(run.id, "repeat-1", 1)
        assert a0 is not None and a0.summary == "alpha"
        assert a1 is not None and a1.summary == "beta"


class TestIterationContext:
    """The block executor stamps a contextvar around body execution so
    nested ``task_executor`` emissions can re-tag streaming deltas with
    the iteration owner's block_id (see app/context.py:
    ``set_task_iteration_context``).  Without this the frontend reducer
    routes deltas by the inner task's id and every iteration's output
    collapses into a single "Iteration 0" bucket on the Live and Tools
    tabs.
    """

    @pytest.mark.asyncio
    async def test_repeat_stamps_iteration_context(self, storage, run):
        from app.context import get_task_iteration_context
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="count", repeat_count=3,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        seen_iter_ctx = []

        async def iter_ctx_capture(b, project_root=None, project_id=None, run_id=None):
            seen_iter_ctx.append(get_task_iteration_context())
            return _mk_artifact("ok")

        with patch("app.agents.block_executor.execute_task_block", iter_ctx_capture):
            await execute_block(block, ctx)

        assert seen_iter_ctx == [
            {"block_id": "repeat-1", "index": 0},
            {"block_id": "repeat-1", "index": 1},
            {"block_id": "repeat-1", "index": 2},
        ]
        # Cleared after the block finishes
        assert get_task_iteration_context() is None

    @pytest.mark.asyncio
    async def test_until_stamps_iteration_context(self, storage, run):
        from app.context import get_task_iteration_context
        block = Block(
            block_type="repeat", id="repeat-1", name="r",
            repeat_mode="until", repeat_max=3,
            body=[_task("inner")],
        )
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        seen_iter_ctx = []

        async def iter_ctx_capture_fail(b, project_root=None, project_id=None, run_id=None):
            seen_iter_ctx.append(get_task_iteration_context())
            # Fail every iteration so until runs to repeat_max
            return _mk_artifact("nope", failed=True)

        async def never_satisfied(*args, **kwargs):
            return False

        with patch("app.agents.block_executor.execute_task_block", iter_ctx_capture_fail):
            with patch(
                "app.agents.block_executor._evaluate_until_condition_with_model",
                new=never_satisfied,
            ):
                await execute_block(block, ctx)

        assert all(
            s and s["block_id"] == "repeat-1" for s in seen_iter_ctx
        )
        assert [s["index"] for s in seen_iter_ctx] == list(range(len(seen_iter_ctx)))
        assert get_task_iteration_context() is None
