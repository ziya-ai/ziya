"""Tests for sequence on_failure policy and artifact-sourced for_each.

Covers:
- on_failure="stop" halts a Group's body at the first failed child
- default (no on_failure) preserves legacy run-everything behaviour
- on_failure="stop" applies inside a Repeat body each iteration
- for_each source templating: a planner sibling's artifact drives the
  fan-out via {{sibling("id")}}, including embedded-array extraction
- an unresolvable TEMPLATED source fails the Repeat block, while an
  empty resolved list runs zero iterations without failing
- parse_for_each_source embedded-array extraction unit cases
"""

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, execute_block, _plan_iterations
from app.agents.task_templating import parse_for_each_source
from app.models.task_card import Artifact, Block


@pytest.fixture
def fake_tasks(monkeypatch):
    """Replace the model-invoking task executor with a recorder.

    A task whose name contains "FAIL" returns a failed artifact; all
    others succeed with summary == their (rendered) instructions.
    """
    calls = []

    async def _fake(block, **kwargs):
        calls.append({"name": block.name, "instructions": block.instructions or ""})
        failed = "FAIL" in (block.name or "")
        return Artifact(
            summary=(block.instructions or block.name or ""),
            failed=failed,
            created_at=0.0,
        )

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    return calls


def _task(id_, name, instructions):
    return Block(block_type="task", id=id_, name=name, instructions=instructions)


def _ctx():
    # storage=None short-circuits cancel_requested and persistence;
    # run_id="" disables relay emission.
    return ExecutionContext(run_id="")


# ── on_failure policy ─────────────────────────────────────────

async def test_group_on_failure_stop_halts_sequence(fake_tasks):
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "first", "one"),
        _task("t2", "FAIL-mid", "two"),
        _task("t3", "never", "three"),
    ])
    result = await execute_block(grp, _ctx())
    names = [c["name"] for c in fake_tasks]
    assert names == ["first", "FAIL-mid"]
    assert result.failed
    assert any("skipped (on_failure=stop)" in d for d in result.decisions)


async def test_group_default_continues_past_failure(fake_tasks):
    grp = Block(block_type="group", id="g", body=[
        _task("t1", "first", "one"),
        _task("t2", "FAIL-mid", "two"),
        _task("t3", "after", "three"),
    ])
    result = await execute_block(grp, _ctx())
    names = [c["name"] for c in fake_tasks]
    assert names == ["first", "FAIL-mid", "after"]
    # Sequence result is the LAST sibling's artifact (which succeeded).
    assert not result.failed


async def test_group_stop_with_failure_at_last_step_adds_no_skip_note(fake_tasks):
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "first", "one"),
        _task("t2", "FAIL-last", "two"),
    ])
    result = await execute_block(grp, _ctx())
    assert result.failed
    assert not any("skipped" in d for d in result.decisions)


async def test_repeat_body_on_failure_stop_gates_each_iteration(fake_tasks):
    rpt = Block(
        block_type="repeat", id="r", repeat_mode="count", repeat_count=2,
        on_failure="stop",
        body=[
            _task("a", "FAIL-first", "x"),
            _task("b", "second", "y"),
        ],
    )
    await execute_block(rpt, _ctx())
    names = [c["name"] for c in fake_tasks]
    assert names == ["FAIL-first", "FAIL-first"]


# ── artifact-sourced for_each ─────────────────────────────────

async def test_for_each_from_planner_sibling_artifact(fake_tasks):
    plan = _task("plan", "plan", 'Plan ready: ["alpha", "beta"]')
    rpt = Block(
        block_type="repeat", id="r", repeat_mode="for_each",
        repeat_for_each_source='{{sibling("plan")}}',
        body=[_task("echo", "echo", "Process {{item}} now")],
    )
    grp = Block(block_type="group", id="g", body=[plan, rpt])
    await execute_block(grp, _ctx())
    echo_calls = [c for c in fake_tasks if c["name"] == "echo"]
    assert len(echo_calls) == 2
    assert "Process alpha now" in echo_calls[0]["instructions"]
    assert "Process beta now" in echo_calls[1]["instructions"]


async def test_for_each_unresolvable_templated_source_fails_block(fake_tasks):
    """An unresolvable templated source must FAIL, not silently degrade.

    The historical behaviour was a fallback to count-based iteration,
    which ran the body with {{item}} rendering to empty string — so a
    fan-out whose upstream list never materialised looked like it had
    done its work.  With repeat_max set as a safety bound that meant
    repeat_max bogus iterations, each burning a full model turn.
    """
    rpt = Block(
        block_type="repeat", id="r", repeat_mode="for_each",
        repeat_for_each_source='{{sibling("nonexistent")}}',
        repeat_max=60,
        body=[_task("echo", "echo", "Process {{item}}")],
    )
    artifact = await execute_block(rpt, _ctx())
    # Nothing ran: the failure is reported instead of manufactured work.
    assert [c for c in fake_tasks if c["name"] == "echo"] == []
    assert artifact.failed is True
    # The source text is surfaced so the author can see what to fix.
    assert "nonexistent" in artifact.summary


async def test_for_each_empty_resolved_list_runs_nothing_without_failing(
    fake_tasks,
):
    """An empty list is a legitimate planner result, not a defect.

    Distinct from the unresolvable case above: the upstream task ran,
    emitted a list, and the list was empty ("nothing to do").  Failing
    here would halt an enclosing on_failure="stop" sequence over a
    correct outcome.
    """
    plan = _task("plan", "plan", "Nothing to process: []")
    rpt = Block(
        block_type="repeat", id="r", repeat_mode="for_each",
        repeat_for_each_source='{{sibling("plan")}}',
        body=[_task("echo", "echo", "Process {{item}}")],
    )
    grp = Block(block_type="group", id="g", body=[plan, rpt])
    await execute_block(grp, _ctx())
    assert [c for c in fake_tasks if c["name"] == "echo"] == []


def test_plan_iterations_without_ctx_uses_raw_source():
    blk = Block(
        block_type="repeat", id="r", repeat_mode="for_each",
        repeat_for_each_source='["x", "y"]',
    )
    iters = _plan_iterations(blk)
    assert [d["item"] for d in iters] == ["x", "y"]


# ── parse_for_each_source extraction unit cases ───────────────

def test_parse_direct_json_array():
    assert parse_for_each_source('["a", "b"]') == ["a", "b"]


def test_parse_embedded_array_in_prose():
    assert parse_for_each_source('The plan: ["a", "b"] — go.') == ["a", "b"]


def test_parse_array_inside_json_object():
    assert parse_for_each_source('{"items": [1, 2]}') == [1, 2]


def test_parse_skips_unparseable_bracket_runs():
    assert parse_for_each_source('see [not json] then [1, 2]') == [1, 2]


def test_parse_no_array_returns_none():
    assert parse_for_each_source("no array here") is None


def test_parse_empty_and_none_return_none():
    assert parse_for_each_source("") is None
    assert parse_for_each_source("   ") is None
    assert parse_for_each_source(None) is None
