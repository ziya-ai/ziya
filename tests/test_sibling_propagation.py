"""
Tests for sequence sibling-result propagation.

A sibling in a sequence (group/loop body) that runs after another block
should be able to see the prior sibling's result.  This is the
"print the final count" case: a Task placed after an Until loop must see
the loop's final artifact.

Two surfaces:
  - Prose auto-injection: the prior sibling's summary is prepended to the
    next sibling's instructions as a standing-context preamble, with NO
    {{}} templating required (matches how State prose / iteration context
    already work).  This is what the count card actually needs.
  - Explicit templating: {{previous_sibling}} / .summary / .decisions
    resolve against the prior sibling's artifact.

Stack discipline: a nested sequence's last sibling must NOT leak to the
enclosing sequence's next sibling.
"""

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate
from app.agents.block_executor import execute_block, ExecutionContext
from app.agents import task_templating
from app.agents.task_templating import IterationBindings
from app.storage.task_runs import TaskRunStorage


# ── Pure templating: {{previous_sibling}} resolution ────────────

def test_previous_sibling_bare_renders_summary():
    b = IterationBindings(
        previous_sibling=Artifact(summary="final count is 301", failed=False))
    assert task_templating.render("{{previous_sibling}}", b) == "final count is 301"


def test_previous_sibling_summary_field():
    b = IterationBindings(
        previous_sibling=Artifact(summary="301", failed=False))
    assert task_templating.render("{{previous_sibling.summary}}", b) == "301"


def test_previous_sibling_decisions_field():
    b = IterationBindings(
        previous_sibling=Artifact(
            summary="x", decisions=["chose A", "then B"], failed=False))
    out = task_templating.render("{{previous_sibling.decisions}}", b)
    assert "chose A" in out and "then B" in out


def test_previous_sibling_none_renders_empty():
    # First sibling / no enclosing sequence → empty, never crashes.
    b = IterationBindings(previous_sibling=None)
    assert task_templating.render("before {{previous_sibling}} after", b) == "before  after"


def test_previous_sibling_unknown_field_empty():
    b = IterationBindings(
        previous_sibling=Artifact(summary="x", failed=False))
    assert task_templating.render("{{previous_sibling.bogus}}", b) == ""


def test_previous_sibling_distinct_from_previous():
    # previous (loop iteration) and previous_sibling (sequence) are
    # independent channels and must not alias.
    b = IterationBindings(
        previous=Artifact(summary="iter-prev", failed=False),
        previous_sibling=Artifact(summary="seq-prev", failed=False))
    assert task_templating.render("{{previous.summary}}", b) == "iter-prev"
    assert task_templating.render("{{previous_sibling.summary}}", b) == "seq-prev"


# ── Executor integration ────────────────────────────────────────

@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def ctx(storage):
    run = storage.create(TaskRunCreate(card_id="c"))
    return ExecutionContext(run_id=run.id, storage=storage)


def _task(id: str, instr: str = "do it") -> Block:
    return Block(block_type="task", id=id, name=id, instructions=instr)


def _group(id: str, body: list) -> Block:
    return Block(block_type="group", id=id, name=id, body=body)


def _recording_stub(summaries_by_id: dict):
    """Stub execute_task_block: return a canned summary per block id AND
    record the (possibly templated/preamble-injected) instructions each
    task actually received, keyed by block id."""
    received: dict = {}

    async def _stub(block, project_root=None, project_id=None, run_id=None):
        received[block.id] = block.instructions
        return Artifact(summary=summaries_by_id.get(block.id, "ok"), failed=False)

    return _stub, received


@pytest.mark.asyncio
async def test_prose_sibling_autocontext_injected(ctx):
    """The count card's exact shape: a task after a prior block, using
    PROSE (no {{}}), receives the prior sibling's summary as preamble."""
    stub, received = _recording_stub({"a": "the final count is 301"})
    group = _group("g", [
        _task("a", "increase the count"),
        _task("b", "print the final count"),  # prose, no template
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    # b's instructions must now carry a's summary as standing context.
    assert "the final count is 301" in received["b"]
    assert "print the final count" in received["b"]  # original prose preserved


@pytest.mark.asyncio
async def test_explicit_previous_sibling_template_resolves(ctx):
    """{{previous_sibling.summary}} in a task's instructions renders to
    the prior sibling's summary at dispatch."""
    stub, received = _recording_stub({"a": "RESULT_42"})
    group = _group("g", [
        _task("a", "compute"),
        _task("b", "the prior result was {{previous_sibling.summary}}"),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    assert "RESULT_42" in received["b"]
    assert "{{previous_sibling" not in received["b"]  # placeholder consumed


@pytest.mark.asyncio
async def test_first_sibling_sees_no_sibling_context(ctx):
    """The first sibling has no prior.  A {{previous_sibling}} reference
    at this position is an authoring mistake (there is no prior sibling),
    so it is preserved VERBATIM — consistent with task_templating's
    "unknown/unavailable placeholders surface to the author" philosophy.
    The key guarantees: no sibling-context preamble is injected, and the
    first sibling is never given a prior it doesn't have."""
    stub, received = _recording_stub({"a": "first"})
    group = _group("g", [
        _task("a", "the prior was {{previous_sibling.summary}}"),
        _task("b", "second"),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    # No sibling-context preamble on the first sibling (the load-bearing
    # guarantee — it must not be handed a prior it doesn't have).
    assert "previous step" not in received["a"]
    # The placeholder is preserved verbatim (no prior → no resolution),
    # surfacing the mis-placed reference rather than silently emptying it.
    assert received["a"] == "the prior was {{previous_sibling.summary}}"


@pytest.mark.asyncio
async def test_three_sibling_chain_sees_immediate_prior(ctx):
    """In A→B→C, C sees B's result (the immediate prior), not A's."""
    stub, received = _recording_stub({"a": "AAA", "b": "BBB"})
    group = _group("g", [
        _task("a", "step a"),
        _task("b", "step b"),
        _task("c", "{{previous_sibling.summary}}"),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    assert "BBB" in received["c"]
    assert "AAA" not in received["c"]


@pytest.mark.asyncio
async def test_nested_sequence_does_not_leak_to_outer(ctx):
    """A nested group's last sibling must not become the outer
    sequence's prior-sibling.  Outer [inner-group, T]; T should see the
    inner GROUP's artifact (its last child's summary), not be corrupted
    by the stack — and the inner group's internal siblings must stay
    isolated within it."""
    stub, received = _recording_stub({
        "x": "inner-x", "y": "inner-y", "t": "tail",
    })
    inner = _group("inner", [_task("x", "ix"), _task("y", "{{previous_sibling.summary}}")])
    outer = _group("outer", [inner, _task("t", "{{previous_sibling.summary}}")])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(outer, ctx)
    # Inner y saw inner x (isolation within the nested sequence).
    assert "inner-x" in received["y"]
    # Outer t saw the inner GROUP's result = its last child y's summary.
    assert "inner-y" in received["t"]


@pytest.mark.asyncio
async def test_empty_summary_prior_injects_no_preamble(ctx):
    """A prior sibling whose summary is empty should not inject an empty
    '[Result of the previous step]' preamble."""
    stub, received = _recording_stub({"a": ""})
    group = _group("g", [_task("a", "noop"), _task("b", "do thing")])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    assert "previous step" not in received["b"]
    assert received["b"] == "do thing"  # unchanged
