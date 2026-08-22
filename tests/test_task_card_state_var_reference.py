"""
Tests for reaching a declared State variable's VALUE from a card.

Regression origin: the "Music notation: full-score fidelity campaign" run
(2e1fbe76) declared ``DEPLOY_COMMAND`` on a State block and referenced it
as ``{{DEPLOY_COMMAND}}`` inside that block's ``state_context`` prose.
Two independent faults made the value unreachable, and both were silent:

  1. a BARE ``{{NAME}}`` reference was treated as an unknown placeholder
     head and left literal, even though NAME was a declared variable —
     only ``{{var.NAME}}`` resolved;
  2. ``state_context`` prose was injected verbatim and never rendered, so
     even the correct ``{{var.NAME}}`` form stayed literal there.

The agent therefore read the characters "{{DEPLOY_COMMAND}}" as its deploy
command for 39 iterations.  These tests pin both halves, plus the
properties that must NOT change: reserved heads still win over a
same-named variable, and a genuinely undeclared name still stays literal
so authoring typos surface.
"""

import asyncio

from app.agents import block_executor, task_templating
from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block


def _task(instr: str, id_: str = "t1") -> Block:
    return Block(block_type="task", id=id_, name="t", instructions=instr, body=[])


def _state(*, variables: dict | None = None, context: str | None = None,
           id_: str = "s1") -> Block:
    return Block(block_type="state", id=id_, name="s",
                 state_variables=variables, state_context=context, body=[])


def _seq(body, id_="r1") -> Block:
    """A count=1 Repeat is the established way to stack siblings."""
    return Block(block_type="repeat", id=id_, name="r", repeat_mode="count",
                 repeat_count=1, repeat_propagate="none", body=body)


def _capture(monkeypatch):
    captured: list = []

    async def fake_exec(block, **kwargs):
        captured.append(block.instructions)
        return Artifact(summary="ok", created_at=0.0)

    monkeypatch.setattr(block_executor, "execute_task_block", fake_exec)
    return captured


DEPLOY = "cp -R frontend/build/. /srv/app/templates/"


# --------------------------------------------------------------------------
# Fault 1: bare {{NAME}} for a declared variable
# --------------------------------------------------------------------------

def test_bare_declared_variable_resolves_in_render():
    b = task_templating.IterationBindings(variables={"DEPLOY_COMMAND": DEPLOY})
    assert task_templating.render("run: {{DEPLOY_COMMAND}}", b) == f"run: {DEPLOY}"


def test_bare_and_namespaced_forms_agree():
    b = task_templating.IterationBindings(variables={"DEPLOY_COMMAND": DEPLOY})
    assert (task_templating.render("{{DEPLOY_COMMAND}}", b)
            == task_templating.render("{{var.DEPLOY_COMMAND}}", b))


def test_bare_declared_variable_resolves_in_task_instructions(monkeypatch):
    captured = _capture(monkeypatch)
    root = _seq([
        _state(variables={"DEPLOY_COMMAND": DEPLOY}),
        _task("Deploy with: {{DEPLOY_COMMAND}}"),
    ])
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert DEPLOY in captured[0]
    assert "{{DEPLOY_COMMAND}}" not in captured[0]


def test_bare_declared_variable_drills_into_structure():
    b = task_templating.IterationBindings(
        variables={"CFG": {"cmd": DEPLOY, "hosts": ["a", "b"]}})
    assert task_templating.render("{{CFG.cmd}}", b) == DEPLOY
    # Non-string values render as JSON, not Python repr (parse_for_each_source
    # consumes these).
    assert task_templating.render("{{CFG.hosts}}", b) == '["a", "b"]'


# --- properties that must NOT regress -------------------------------------

def test_undeclared_bare_name_stays_literal():
    # The unknown-placeholder philosophy: a typo must surface to the author
    # rather than silently rendering empty.
    b = task_templating.IterationBindings(variables={"DEPLOY_COMMAND": DEPLOY})
    assert (task_templating.render("{{DEPLOY_COMMAN}}", b)
            == "{{DEPLOY_COMMAN}}")


def test_reserved_heads_are_not_shadowed_by_a_variable():
    # A variable named after a reserved head must not hijack it, since the
    # reserved heads are checked first.
    b = task_templating.IterationBindings(
        index=7, variables={"index": "HIJACKED", "item": "HIJACKED"})
    assert task_templating.render("{{index}}", b) == "7"
    assert "HIJACKED" not in task_templating.render("{{index}} {{item}}", b)


# --------------------------------------------------------------------------
# Fault 2: state_context prose is never rendered
# --------------------------------------------------------------------------

def test_prose_given_resolves_namespaced_variable(monkeypatch):
    captured = _capture(monkeypatch)
    root = _seq([
        _state(variables={"DEPLOY_COMMAND": DEPLOY},
               context="DEPLOY_COMMAND: {{var.DEPLOY_COMMAND}}"),
        _task("Rebuild and deploy."),
    ])
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert DEPLOY in captured[0]
    assert "{{var.DEPLOY_COMMAND}}" not in captured[0]


def test_prose_given_resolves_bare_variable_the_music_card_shape(monkeypatch):
    # The exact shape that failed in run 2e1fbe76: bare reference, in prose.
    captured = _capture(monkeypatch)
    root = _seq([
        _state(variables={"DEPLOY_COMMAND": DEPLOY, "MAX_FIXES": "40"},
               context=("RENDER/VERIFY PATH. A source edit is invisible "
                        "until deployed.\n\nDEPLOY_COMMAND: "
                        "{{DEPLOY_COMMAND}}")),
        _task("Pick the next defect and implement it."),
    ])
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert DEPLOY in captured[0]
    assert "{{DEPLOY_COMMAND}}" not in captured[0]
    # The surrounding prose is still delivered intact.
    assert "source edit is invisible" in captured[0]


def test_launch_override_wins_in_prose(monkeypatch):
    # Overrides beat the authored baseline in instructions; prose must not
    # disagree with instructions about the effective value.
    captured = _capture(monkeypatch)
    root = _seq([
        _state(variables={"DEPLOY_COMMAND": "REPLACE_ME"},
               context="DEPLOY_COMMAND: {{DEPLOY_COMMAND}}"),
        _task("Deploy."),
    ])
    ctx = ExecutionContext(run_id="r")
    ctx.overrides["DEPLOY_COMMAND"] = DEPLOY
    asyncio.run(execute_block(root, ctx))
    assert DEPLOY in captured[0]
    assert "REPLACE_ME" not in captured[0]


def test_prose_without_placeholders_is_unchanged(monkeypatch):
    # Rendering prose must be a no-op for the common case, so the change
    # cannot perturb existing cards.
    captured = _capture(monkeypatch)
    prose = "Assume prod, migration already ran, flag is off."
    root = _seq([_state(context=prose), _task("Deploy the service.")])
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert prose in captured[0]


def test_unresolvable_placeholder_in_prose_stays_literal(monkeypatch):
    # Same typo-surfacing contract as instructions: an undeclared name in
    # prose stays visible rather than vanishing.
    captured = _capture(monkeypatch)
    root = _seq([
        _state(variables={"DEPLOY_COMMAND": DEPLOY},
               context="Use {{NOT_DECLARED}} to deploy."),
        _task("Deploy."),
    ])
    asyncio.run(execute_block(root, ExecutionContext(run_id="r")))
    assert "{{NOT_DECLARED}}" in captured[0]
