"""``until_mode="expression"``: a shell command is the loop's exit gate.

Until 2026-11 the branch logged ``until_mode='expression' not yet
implemented; running to max`` and the loop ran to ``until_max`` whatever
the author wrote.  Now ``until_condition`` is run as a command after each
iteration (app/agents/until_gate.py): exit 0 stops the loop, anything
else continues it and the command's output is the rationale fed to the
next iteration.  A command the shell policy refuses stops the loop at
once -- no iteration of the body can make it allowed.
"""
from __future__ import annotations

import os

import pytest
from unittest.mock import patch

from app.agents import until_gate
from app.agents.block_executor import (
    ExecutionContext, UNTIL_CONDITION_UNMET, execute_block,
)
from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage


def _allow(command, *, project_root, shell_commands):
    return True, "", {"PATH": os.environ.get("PATH", "")}


def _refuse(command, *, project_root, shell_commands):
    return False, "command 'frobnicate' is not in the allowlist", {}


# ---------------------------------------------------------------- the gate


@pytest.mark.asyncio
async def test_exit_zero_is_satisfied(tmp_path):
    with patch.object(until_gate, "_adjudicate", _allow):
        r = await until_gate.evaluate_command_gate("true", project_root=str(tmp_path))
    assert r.satisfied and r.exit_code == 0 and not r.refused


@pytest.mark.asyncio
async def test_nonzero_exit_carries_output_as_rationale(tmp_path):
    with patch.object(until_gate, "_adjudicate", _allow):
        r = await until_gate.evaluate_command_gate(
            "echo 'FAILED tests/test_x.py::test_y' >&2; exit 3",
            project_root=str(tmp_path))
    assert not r.satisfied and r.exit_code == 3
    assert "exited 3" in r.rationale
    assert "FAILED tests/test_x.py::test_y" in r.rationale


@pytest.mark.asyncio
async def test_runs_in_the_block_cwd(tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "marker").write_text("x")
    with patch.object(until_gate, "_adjudicate", _allow):
        r = await until_gate.evaluate_command_gate(
            "test -f marker", project_root=str(tmp_path),
            cwd=until_gate.gate_cwd(str(tmp_path), "pkg"))
    assert r.satisfied


def test_gate_cwd_confined_to_project_root(tmp_path):
    assert until_gate.gate_cwd(str(tmp_path), "../..") == str(tmp_path)
    assert until_gate.gate_cwd(str(tmp_path), "missing") == str(tmp_path)
    assert until_gate.gate_cwd(str(tmp_path), None) == str(tmp_path)


@pytest.mark.asyncio
async def test_policy_refusal_is_reported_not_run(tmp_path):
    ran = []
    with patch.object(until_gate, "_adjudicate", _refuse), \
         patch.object(until_gate, "_run", lambda *a, **k: ran.append(a)):
        r = await until_gate.evaluate_command_gate("frobnicate", project_root=str(tmp_path))
    assert r.refused and not r.satisfied
    assert "not in the allowlist" in r.rationale
    assert ran == []


@pytest.mark.asyncio
async def test_timeout_is_unsatisfied_not_refused(tmp_path):
    with patch.object(until_gate, "_adjudicate", _allow):
        r = await until_gate.evaluate_command_gate(
            "sleep 5", project_root=str(tmp_path), timeout_secs=0.3)
    assert r.timed_out and not r.satisfied and not r.refused


@pytest.mark.asyncio
async def test_empty_command_is_refused(tmp_path):
    r = await until_gate.evaluate_command_gate("   ", project_root=str(tmp_path))
    assert r.refused


@pytest.mark.asyncio
async def test_real_policy_refuses_what_the_shell_tool_refuses(tmp_path):
    """The gate is adjudicated by the shell tool's own policy, so an
    always-blocked command is refused without running."""
    r = await until_gate.evaluate_command_gate(
        "sudo rm -rf /", project_root=str(tmp_path))
    assert r.refused, r
    assert not r.satisfied


@pytest.mark.asyncio
async def test_real_policy_admits_an_allowlisted_command(tmp_path):
    r = await until_gate.evaluate_command_gate("echo ok", project_root=str(tmp_path))
    assert not r.refused, r.rationale
    assert r.satisfied


# ----------------------------------------------------- through the executor


@pytest.fixture
def ctx(tmp_path):
    storage = TaskRunStorage(tmp_path / "runs")
    run = storage.create(TaskRunCreate(card_id="c"))
    storage.update_status(run.id, "running")
    return ExecutionContext(run_id=run.id, storage=storage, project_root=str(tmp_path))


def _until(condition: str, n_max: int, **extra) -> Block:
    return Block(
        block_type="until", id="u", name="repair",
        until_mode="expression", until_condition=condition, until_max=n_max,
        body=[Block(block_type="task", id="inner", name="fix", instructions="fix")],
        **extra,
    )


@pytest.mark.asyncio
async def test_loop_stops_when_the_command_passes(ctx, tmp_path):
    """The body 'fixes' on its second pass; the gate sees it and stops."""
    marker = tmp_path / "green"
    calls = [0]

    async def body(block, **kw):
        calls[0] += 1
        if calls[0] == 2:
            marker.write_text("ok")
        return Artifact(summary=f"attempt {calls[0]}")

    events = []
    with patch("app.agents.block_executor.execute_task_block", side_effect=body), \
         patch.object(until_gate, "_adjudicate", _allow), \
         patch("app.agents.block_executor._emit", side_effect=lambda c, e: events.append(e)) as em:
        async def _aemit(c, e):
            events.append(e)
        em.side_effect = _aemit
        art = await execute_block(_until(f"test -f {marker}", 5), ctx)

    assert calls[0] == 2
    assert not art.failed
    assert any("satisfied at iter 1" in d for d in art.decisions)
    assert not any("not yet implemented" in d for d in art.decisions)
    evals = [e for e in events if e.get("type") == "until_evaluated"]
    assert [e["satisfied"] for e in evals] == [False, True]
    assert all(e["mode"] == "expression" for e in evals)


@pytest.mark.asyncio
async def test_failing_output_is_fed_back_as_rationale(ctx):
    """The next iteration is told what the gate printed, the way a model
    evaluator's reason is."""
    seen_rationale = []

    async def body(block, **kw):
        bindings = ctx.binding_stack[-1]
        seen_rationale.append(bindings.until_rationale)
        return Artifact(summary=f"attempt {len(seen_rationale)}")

    with patch("app.agents.block_executor.execute_task_block", side_effect=body), \
         patch.object(until_gate, "_adjudicate", _allow):
        art = await execute_block(
            _until("echo '2 failed, 7 passed' ; exit 1", 2), ctx)

    assert seen_rationale[0] is None
    assert "2 failed, 7 passed" in (seen_rationale[1] or "")
    assert art.failed and art.signature == UNTIL_CONDITION_UNMET


@pytest.mark.asyncio
async def test_exhaustion_with_gate_unmet_fails_the_block(ctx):
    async def body(block, **kw):
        return Artifact(summary="still red")

    with patch("app.agents.block_executor.execute_task_block", side_effect=body), \
         patch.object(until_gate, "_adjudicate", _allow):
        art = await execute_block(_until("false", 3), ctx)
    assert art.failed
    assert art.signature == UNTIL_CONDITION_UNMET
    assert any("the goal was not reached" in d for d in art.decisions)


@pytest.mark.asyncio
async def test_refused_gate_stops_after_one_iteration(ctx):
    """A command the policy refuses cannot be made to pass by the body, so
    the loop does not spend until_max iterations finding that out."""
    calls = [0]

    async def body(block, **kw):
        calls[0] += 1
        return Artifact(summary=f"attempt {calls[0]}")

    with patch("app.agents.block_executor.execute_task_block", side_effect=body), \
         patch.object(until_gate, "_adjudicate", _refuse):
        art = await execute_block(_until("frobnicate --fix", 5), ctx)
    assert calls[0] == 1
    assert art.failed and art.signature == UNTIL_CONDITION_UNMET
    assert any("gate command cannot run" in d for d in art.decisions)
    assert any("not in the allowlist" in d for d in art.decisions)


@pytest.mark.asyncio
async def test_block_scope_grants_reach_the_policy_check(ctx):
    """The Until block's own (authorized) scope supplies the task-scope
    grants the policy consults."""
    from app.models.task_card import TaskScope
    seen = {}

    def spy(command, *, project_root, shell_commands):
        seen["grants"] = list(shell_commands or [])
        return True, "", {}

    async def body(block, **kw):
        return Artifact(summary="x")

    block = _until("true", 2, scope=TaskScope(shell_commands=["pytest"]))
    with patch("app.agents.block_executor.execute_task_block", side_effect=body), \
         patch("app.utils.scope_approvals.authorize_scope", lambda bid, s, **k: s), \
         patch.object(until_gate, "_adjudicate", spy):
        await execute_block(block, ctx)
    assert seen["grants"] == ["pytest"]


# -------------------------------------------------------------- validation


def test_expression_mode_without_a_command_is_a_launch_error():
    from app.utils.task_card_validation import validate_card_tree
    root = Block(
        block_type="until", id="u", name="u", until_mode="expression",
        until_condition="", until_max=2,
        body=[Block(block_type="task", id="t", name="t", instructions="x")],
    )
    res = validate_card_tree(root)
    assert any("expression mode" in f.message for f in res.errors), res
    # A model-mode block with no condition is still the legitimate /goal shape.
    root.until_mode = "model"
    assert not validate_card_tree(root).errors
