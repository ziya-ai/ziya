"""Tests for templating + propagation integration in the block executor.

Stubs execute_task_block to capture the instructions actually handed
to the task runtime.  This is the only place we verify that the
substituted text (not the authored template) reaches the model.
"""

import pytest
from unittest.mock import patch

from app.models.task_card import Block, Artifact
from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.storage.task_runs import TaskRunStorage
from app.agents.block_executor import execute_block, ExecutionContext


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="card-1"))
    return r


def _repeat(
    body_task: Block,
    *,
    mode: str = "count",
    count: int = 3,
    propagate: str = "none",
    for_each_source: str | None = None,
    parallel: bool = False,
) -> Block:
    return Block(
        block_type="repeat",
        id="repeat-1",
        name="Loop",
        repeat_mode=mode,
        repeat_count=count,
        repeat_propagate=propagate,
        repeat_for_each_source=for_each_source,
        repeat_parallel=parallel,
        body=[body_task],
    )


def _task(instructions: str) -> Block:
    return Block(block_type="task", id="task-1", name="T", instructions=instructions)


def _capturing_stub(captured: list[str], summaries: list[str] | None = None):
    """Stub for execute_task_block that records the instructions it
    received and returns an Artifact whose summary is pre-canned per
    iteration."""
    call_idx = {"n": 0}
    canned = summaries or []

    async def _stub(block, project_root=None, project_id=None):
        captured.append(block.instructions or "")
        idx = call_idx["n"]
        call_idx["n"] += 1
        s = canned[idx] if idx < len(canned) else f"iter-{idx}"
        return Artifact(summary=s, duration_ms=1)

    return _stub


def _seed_block_states(storage: TaskRunStorage, run_id: str, block: Block) -> None:
    if block.id:
        storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type,
        ))
    for c in block.body or []:
        _seed_block_states(storage, run_id, c)


class TestIndexSubstitution:
    @pytest.mark.asyncio
    async def test_index_substituted_per_iteration(self, storage, run):
        block = _repeat(_task("run #{{index}}"), count=3)
        _seed_block_states(storage, run.id, block)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured)):
            await execute_block(block, ctx)
        assert captured == ["run #0", "run #1", "run #2"]

    @pytest.mark.asyncio
    async def test_no_bindings_no_substitution(self, storage, run):
        # A bare task (not inside a Repeat) must not alter instructions.
        task = _task("plain {{index}}")
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id="task-1", block_type="task",
        ))
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured)):
            await execute_block(task, ctx)
        # Placeholder preserved verbatim because no Repeat is active.
        assert captured == ["plain {{index}}"]


class TestPropagation:
    @pytest.mark.asyncio
    async def test_propagate_none(self, storage, run):
        block = _repeat(_task("prev={{previous.summary}}"),
                        count=3, propagate="none")
        _seed_block_states(storage, run.id, block)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured, ["first", "second", "third"])):
            await execute_block(block, ctx)
        # propagate=none → previous is always empty.
        assert captured == ["prev=", "prev=", "prev="]

    @pytest.mark.asyncio
    async def test_propagate_last(self, storage, run):
        block = _repeat(_task("prev={{previous.summary}}"),
                        count=3, propagate="last")
        _seed_block_states(storage, run.id, block)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured, ["first", "second", "third"])):
            await execute_block(block, ctx)
        assert captured == ["prev=", "prev=first", "prev=second"]

    @pytest.mark.asyncio
    async def test_propagate_all_summaries(self, storage, run):
        block = _repeat(_task("history:\n{{all.summaries}}"),
                        count=3, propagate="all")
        _seed_block_states(storage, run.id, block)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured, ["A", "B", "C"])):
            await execute_block(block, ctx)
        assert captured[0] == "history:\n"
        assert captured[1] == "history:\nA"
        assert captured[2] == "history:\nA\n\nB"


class TestForEachSource:
    @pytest.mark.asyncio
    async def test_for_each_items_bound(self, storage, run):
        block = _repeat(
            _task("process {{item}}"),
            mode="for_each",
            for_each_source='["alpha", "beta", "gamma"]',
        )
        _seed_block_states(storage, run.id, block)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured)):
            await execute_block(block, ctx)
        assert captured == ["process alpha", "process beta", "process gamma"]

    @pytest.mark.asyncio
    async def test_for_each_dict_items(self, storage, run):
        block = _repeat(
            _task("{{item.name}}={{item.value}}"),
            mode="for_each",
            for_each_source='[{"name": "x", "value": 1}, {"name": "y", "value": 2}]',
        )
        _seed_block_states(storage, run.id, block)
        ctx = ExecutionContext(run_id=run.id, storage=storage)
        captured: list[str] = []
        with patch("app.agents.block_executor.execute_task_block",
                   _capturing_stub(captured)):
            await execute_block(block, ctx)
        assert captured == ["x=1", "y=2"]
