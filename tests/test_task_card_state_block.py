"""
Tests for the State block — read-only run-scoped variables in Task Cards.

Two layers:
  1. Pure templating: {{var.NAME}} / {{var.NAME.sub}} resolution and the
     unknown-key-left-literal contract (app/agents/task_templating.py).
  2. Executor placement-as-policy: a State block sets ctx.variables; a
     task reads it; State inside a Repeat re-applies its literals every
     iteration (reset), while an outer State persists across the loop
     (app/agents/block_executor.py::_execute_state).

The executor tests monkeypatch the module-local ``execute_task_block``
reference so a Task leaf records the *effective* (post-templating)
instructions it would have been dispatched with — no model invocation.
"""

import asyncio

from app.models.task_card import Artifact, Block
from app.agents import block_executor
from app.agents.block_executor import ExecutionContext, execute_block
from app.agents.task_templating import IterationBindings, render


# ── Layer 1: templating ────────────────────────────────────

def test_var_resolves_scalar_and_index_together():
    b = IterationBindings(index=3, variables={"target": "prod"})
    out = render("deploy {{var.target}} at iter {{index}}", b)
    assert out == "deploy prod at iter 3"


def test_var_resolves_nested_dict_field():
    b = IterationBindings(variables={"cfg": {"region": "us-east-1"}})
    assert render("region={{var.cfg.region}}", b) == "region=us-east-1"


def test_unknown_var_key_is_left_literal():
    # Known head, unknown key → preserved verbatim so typos surface,
    # matching the module's unknown-placeholder philosophy.
    b = IterationBindings(variables={"target": "prod"})
    assert render("{{var.typo}}", b) == "{{var.typo}}"


def test_bare_var_is_left_literal():
    b = IterationBindings(variables={"x": "1"})
    assert render("{{var}}", b) == "{{var}}"


def test_var_renders_when_no_loop_bindings_present():
    # A top-level task (no Repeat/Until) must still resolve {{var.X}}.
    b = IterationBindings(variables={"name": "Ziya"})
    assert render("hi {{var.name}}", b) == "hi Ziya"


# ── Layer 2: executor placement-as-policy ──────────────────

def _task(instr: str, id_: str = "t1") -> Block:
    return Block(block_type="task", id=id_, name="t", instructions=instr, body=[])


def _state(variables: dict, id_: str = "s1") -> Block:
    return Block(block_type="state", id=id_, name="s",
                 state_variables=variables, body=[])


def _repeat(body, count=1, id_="r1") -> Block:
    return Block(block_type="repeat", id=id_, name="r", repeat_mode="count",
                 repeat_count=count, repeat_propagate="none", body=body)


def _capture(monkeypatch):
    """Patch execute_task_block to record effective instructions."""
    captured: list = []

    async def fake_exec(block, **kwargs):
        captured.append(block.instructions)
        return Artifact(summary="ok", created_at=0.0)

    monkeypatch.setattr(block_executor, "execute_task_block", fake_exec)
    return captured


def test_state_sets_variable_read_by_following_task(monkeypatch):
    captured = _capture(monkeypatch)
    root = _repeat([_state({"target": "prod"}),
                    _task("deploy {{var.target}}")], count=1)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert len(captured) == 1
    assert "deploy prod" in captured[0]


def test_state_inside_loop_resets_each_iteration(monkeypatch):
    captured = _capture(monkeypatch)
    # State re-runs at the top of every iteration → same baseline value
    # each cycle, even though it sits inside a 3-iteration loop.
    root = _repeat([_state({"n": "0"}), _task("v={{var.n}}")], count=3)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert len(captured) == 3
    assert all("v=0" in c for c in captured)


def test_outer_state_persists_inner_state_resets(monkeypatch):
    captured = _capture(monkeypatch)
    # Outer State (once) + inner Repeat with its own State (reset).
    inner = _repeat([_state({"retries": "0"}, id_="s2"),
                     _task("{{var.target}}/{{var.retries}}", id_="t2")],
                    count=2, id_="rin")
    root = _repeat([_state({"target": "prod"}, id_="s1"), inner], count=1, id_="rout")
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert len(captured) == 2
    assert all(c == "prod/0" for c in captured)


def test_task_without_state_or_loop_is_unchanged(monkeypatch):
    captured = _capture(monkeypatch)
    # A bare task with no variables and no loop must pass through verbatim.
    root = _task("plain instructions, no templating")
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert captured == ["plain instructions, no templating"]


def test_state_block_returns_noncfailed_artifact(monkeypatch):
    _capture(monkeypatch)
    art = asyncio.run(execute_block(_state({"a": "1"}), ExecutionContext(run_id="r")))
    assert art.failed is False
    assert "a" in art.summary


# ── Layer 3: launch-time parameter_overrides win over State ──

def test_override_wins_over_state_authored_value(monkeypatch):
    # A State block authors target=prod; a launch override sets it to
    # staging.  The override must win at read time.
    captured = _capture(monkeypatch)
    root = _repeat([_state({"target": "prod"}),
                    _task("deploy {{var.target}}")], count=1)
    asyncio.run(execute_block(
        root, ExecutionContext(run_id="r", overrides={"target": "staging"})))
    assert "deploy staging" in captured[0]


def test_override_survives_loop_baseline_reapply(monkeypatch):
    # State inside a loop re-applies its baseline each iteration, but the
    # override still layers on top every cycle — override wins throughout.
    captured = _capture(monkeypatch)
    root = _repeat([_state({"n": "0"}),
                    _task("n={{var.n}}")], count=3)
    asyncio.run(execute_block(
        root, ExecutionContext(run_id="r", overrides={"n": "9"})))
    assert captured == ["n=9", "n=9", "n=9"]


def test_override_with_no_state_block_is_readable(monkeypatch):
    # Overrides alone (no State block authored) are still resolvable by a
    # top-level task with no loop — the dead-field-made-live path.
    captured = _capture(monkeypatch)
    root = _task("hello {{var.who}}")
    asyncio.run(execute_block(
        root, ExecutionContext(run_id="r", overrides={"who": "world"})))
    assert captured[0] == "hello world"


def test_no_override_leaves_state_value_intact(monkeypatch):
    # Empty overrides must not disturb the State-authored value.
    captured = _capture(monkeypatch)
    root = _repeat([_state({"target": "prod"}),
                    _task("deploy {{var.target}}")], count=1)
    asyncio.run(execute_block(
        root, ExecutionContext(run_id="r", overrides={})))
    assert "deploy prod" in captured[0]
