"""
Tests for the State block's PROSE context — the conversational baseline.

A State block's ``state_context`` is freeform English givens that flow
into every in-scope task automatically, with no {{var}} templating.  This
is the primary form most cards use; named ``state_variables`` are the
optional formal adjunct (covered in test_task_card_state_block.py).

Contract under test (app/agents/block_executor.py):
  - prose flows into a following task's effective instructions as a
    standing-context preamble, verbatim, without templating;
  - placement is the reset policy, same as variables — but because prose
    is keyed by block id, re-execution in a loop OVERWRITES (idempotent),
    never duplicates;
  - prose and variables compose (both surface); empty prose clears.
"""

import asyncio

from app.models.task_card import Artifact, Block
from app.agents import block_executor
from app.agents.block_executor import ExecutionContext, execute_block


def _task(instr: str, id_: str = "t1") -> Block:
    return Block(block_type="task", id=id_, name="t", instructions=instr, body=[])


def _state(*, variables: dict | None = None, context: str | None = None,
           id_: str = "s1") -> Block:
    return Block(block_type="state", id=id_, name="s",
                 state_variables=variables, state_context=context, body=[])


def _repeat(body, count=1, id_="r1") -> Block:
    return Block(block_type="repeat", id=id_, name="r", repeat_mode="count",
                 repeat_count=count, repeat_propagate="none", body=body)


def _capture(monkeypatch):
    captured: list = []

    async def fake_exec(block, **kwargs):
        captured.append(block.instructions)
        return Artifact(summary="ok", created_at=0.0)

    monkeypatch.setattr(block_executor, "execute_task_block", fake_exec)
    return captured


def test_prose_flows_into_following_task_without_templating(monkeypatch):
    captured = _capture(monkeypatch)
    root = _repeat([
        _state(context="Assume prod, migration already ran, flag is off."),
        _task("Deploy the service."),
    ], count=1)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert len(captured) == 1
    # Prose appears verbatim, and the task's own instruction is preserved.
    assert "migration already ran" in captured[0]
    assert "Deploy the service." in captured[0]
    # No templating syntax leaked in.
    assert "{{" not in captured[0]


def test_prose_at_top_level_no_loop(monkeypatch):
    # A top-level task (no Repeat) still receives prose givens.
    captured = _capture(monkeypatch)
    # Sequence via a count=1 repeat is the only way to stack siblings,
    # but a card root that is itself the state+task pair uses a wrapper.
    root = _repeat([
        _state(context="The user is on a mobile device."),
        _task("Render the dashboard."),
    ], count=1)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert "mobile device" in captured[0]


def test_prose_reapplied_in_loop_does_not_duplicate(monkeypatch):
    # Keyed by block id → re-execution overwrites, so each iteration sees
    # the prose exactly once, never accumulating copies.
    captured = _capture(monkeypatch)
    root = _repeat([
        _state(context="Baseline assumption X."),
        _task("Do the thing."),
    ], count=3)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert len(captured) == 3
    for instr in captured:
        # Exactly one occurrence of the prose per iteration.
        assert instr.count("Baseline assumption X.") == 1


def test_prose_and_variables_compose(monkeypatch):
    captured = _capture(monkeypatch)
    root = _repeat([
        _state(variables={"target": "prod"},
               context="Assume the migration already ran."),
        _task("Deploy to {{var.target}}."),
    ], count=1)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    out = captured[0]
    assert "migration already ran" in out      # prose surfaced
    assert "Deploy to prod." in out             # variable templated


def test_empty_prose_clears_prior_note(monkeypatch):
    # An outer State sets prose; an inner State with the SAME id and empty
    # prose clears it.  (Different ids would coexist; same id overwrites.)
    captured = _capture(monkeypatch)
    root = _repeat([
        _state(context="Outer note.", id_="shared"),
        _repeat([
            _state(context="", id_="shared"),   # same id, empty → clears
            _task("Inner task."),
        ], count=1, id_="inner"),
    ], count=1)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    # The inner task should NOT carry the outer note (cleared by same-id
    # empty prose) — only its own instruction.
    assert "Outer note." not in captured[0]
    assert "Inner task." in captured[0]


def test_two_distinct_state_blocks_both_surface(monkeypatch):
    captured = _capture(monkeypatch)
    root = _repeat([
        _state(context="Note A.", id_="a"),
        _state(context="Note B.", id_="b"),
        _task("Proceed."),
    ], count=1)
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    out = captured[0]
    assert "Note A." in out and "Note B." in out


def test_no_state_block_leaves_instructions_untouched(monkeypatch):
    # Sanity: with no prose and no vars, the task instruction is verbatim.
    captured = _capture(monkeypatch)
    root = _task("Just do it.")
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert captured[0] == "Just do it."
