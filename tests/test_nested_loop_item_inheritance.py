"""A loop nested inside a for_each iteration must not blank {{item}}.

Measured on the memory-replay card (run 8c5deca5, 2026-09-27).  Stage 3
is a parallel for_each over run ids whose body is an Until wrapping a
Task "Run id: {{item}} ...".  The Until pushed its own bindings with
item=None, shadowing the enclosing item, so every Task was told
"Run id: ." -- and its until_condition "run {{item}} reached the end of
its window" was never templated at all.  One iteration then picked a run
on its own and advanced a sibling's.

Driven through the real block executor; only the model boundary
(execute_task_block, the until evaluator) is replaced.
"""
import asyncio
import json
import time

import pytest

import app.agents.block_executor as be
from app.models.task_card import Artifact, Block

RUNS = ["sep-baseline", "sep-current", "sep-current-aa"]


@pytest.fixture
def seen(monkeypatch):
    got = []

    async def _task(block, **kw):
        got.append(block.instructions)
        await asyncio.sleep(0)
        return Artifact(summary=f"did: {block.instructions}", created_at=time.time())

    monkeypatch.setattr(be, "execute_task_block", _task)
    return got


def _task():
    return Block(id="t", block_type="task", name="Advance {{item}}",
                 instructions="Run id: {{item}}.")


def _fanout(inner: Block, parallel=True) -> Block:
    return Block(id="fan", block_type="repeat", name="fan", repeat_mode="for_each",
                 repeat_for_each_source=json.dumps(RUNS), repeat_parallel=parallel,
                 repeat_propagate="none", body=[inner])


def _ctx():
    return be.ExecutionContext(run_id="r", project_id="p", project_root="/tmp")


def _ids(instr):
    return sorted(i.rsplit("Run id: ", 1)[1].rstrip(".") for i in instr)


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [True, False])
async def test_until_inside_for_each_keeps_the_item(seen, parallel):
    until = Block(id="u", block_type="until", name="Chunk {{item}}", until_max=1, body=[_task()])
    await be.execute_block(_fanout(until, parallel), _ctx())
    assert _ids(seen) == sorted(RUNS)


@pytest.mark.asyncio
async def test_count_repeat_inside_for_each_keeps_the_item(seen):
    inner = Block(id="c", block_type="repeat", name="twice", repeat_mode="count",
                  repeat_count=2, repeat_propagate="none", body=[_task()])
    await be.execute_block(_fanout(inner), _ctx())
    assert _ids(seen) == sorted(RUNS * 2)


@pytest.mark.asyncio
async def test_direct_body_unchanged(seen):
    """Positive control: the path that always worked still works."""
    await be.execute_block(_fanout(_task()), _ctx())
    assert _ids(seen) == sorted(RUNS)


@pytest.mark.asyncio
async def test_until_condition_is_templated_with_the_item(seen, monkeypatch):
    asked = []

    async def _judge(condition, artifact):
        asked.append(condition)
        return True

    monkeypatch.setattr(be, "_evaluate_until_condition_with_model", _judge)
    until = Block(id="u", block_type="until", name="Chunk", until_mode="model",
                  until_condition="run {{item}} reached the end of its window",
                  until_max=2, body=[_task()])
    await be.execute_block(_fanout(until), _ctx())
    assert sorted(asked) == sorted(f"run {r} reached the end of its window" for r in RUNS)
