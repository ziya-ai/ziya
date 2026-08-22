"""
Tests for the {{sibling("block-id")}} by-id artifact lookup.

Distinct from {{previous_sibling}} (the immediate prior block in the
same sequence): sibling("id") resolves a run-scoped registry of ANY
completed block's artifact, keyed by block id.  Covered:
  - resolver: bare, .summary, .decisions, double vs single quotes
  - unknown / not-yet-run id renders empty (honest "no result")
  - unknown field on a found artifact renders empty
  - regex matches the function form alongside dotted names
  - executor integration: a later block references an earlier one by id
  - loop re-run: last-write-wins (the lookup sees the latest result)
"""

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate
from app.agents.block_executor import execute_block, ExecutionContext
from app.agents import task_templating
from app.agents.task_templating import IterationBindings
from app.storage.task_runs import TaskRunStorage


# ── Pure resolver ───────────────────────────────────────────────

def _binds(**reg):
    return IterationBindings(sibling_artifacts=dict(reg))


def test_sibling_bare_renders_summary():
    b = _binds(a=Artifact(summary="hello", failed=False))
    assert task_templating.render('{{sibling("a")}}', b) == "hello"


def test_sibling_summary_field():
    b = _binds(a=Artifact(summary="S", failed=False))
    assert task_templating.render('{{sibling("a").summary}}', b) == "S"


def test_sibling_decisions_field():
    b = _binds(a=Artifact(summary="x", decisions=["d1", "d2"], failed=False))
    out = task_templating.render('{{sibling("a").decisions}}', b)
    assert "d1" in out and "d2" in out


def test_sibling_single_quotes_work():
    b = _binds(a=Artifact(summary="sq", failed=False))
    assert task_templating.render("{{sibling('a')}}", b) == "sq"


def test_sibling_hyphenated_id():
    # Real block ids look like t-1a2b3c / u-9f8e7d.
    b = _binds(**{"u-9f8e": Artifact(summary="loop done", failed=False)})
    assert task_templating.render('{{sibling("u-9f8e")}}', b) == "loop done"


def test_sibling_unknown_id_renders_empty():
    b = _binds(a=Artifact(summary="x", failed=False))
    # 'b' never ran → empty, not literal (it's an explicit id reference).
    assert task_templating.render('before {{sibling("b")}} after', b) == "before  after"


def test_sibling_unknown_field_renders_empty():
    b = _binds(a=Artifact(summary="x", failed=False))
    assert task_templating.render('{{sibling("a").bogus}}', b) == ""


def test_sibling_does_not_collide_with_dotted_names():
    # A normal {{var.NAME}} still resolves alongside a sibling() call.
    b = IterationBindings(
        variables={"k": "V"},
        sibling_artifacts={"a": Artifact(summary="A", failed=False)},
    )
    out = task_templating.render('{{var.k}} / {{sibling("a")}}', b)
    assert out == "V / A"


def test_sibling_id_with_dot_not_split():
    # A quoted id containing a dot must not be mangled by the dotted-name
    # splitter (the sibling form is matched before the split).
    b = _binds(**{"a.b": Artifact(summary="dotted", failed=False)})
    assert task_templating.render('{{sibling("a.b")}}', b) == "dotted"


# ── Executor integration ────────────────────────────────────────

@pytest.fixture
def ctx(tmp_path):
    storage = TaskRunStorage(tmp_path)
    run = storage.create(TaskRunCreate(card_id="c"))
    return ExecutionContext(run_id=run.id, storage=storage)


def _task(id: str, instr: str = "do it") -> Block:
    return Block(block_type="task", id=id, name=id, instructions=instr)


def _group(id: str, body: list) -> Block:
    return Block(block_type="group", id=id, name=id, body=body)


def _recording_stub(summaries_by_id: dict):
    received: dict = {}

    async def _stub(block, project_root=None, project_id=None, run_id=None):
        received[block.id] = block.instructions
        return Artifact(summary=summaries_by_id.get(block.id, "ok"), failed=False)

    return _stub, received


@pytest.mark.asyncio
async def test_later_block_references_earlier_by_id(ctx):
    """A non-adjacent later block resolves {{sibling("earlier-id")}}."""
    stub, received = _recording_stub({"first": "FIRST_RESULT", "mid": "m"})
    group = _group("g", [
        _task("first", "compute first"),
        _task("mid", "unrelated middle step"),
        _task("last", 'the first result was {{sibling("first").summary}}'),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    # 'last' saw 'first' by id, even though 'mid' is the immediate prior.
    assert "FIRST_RESULT" in received["last"]
    assert "{{sibling" not in received["last"]


@pytest.mark.asyncio
async def test_sibling_distinct_from_previous_sibling(ctx):
    """previous_sibling = immediate prior (mid); sibling("first") = by id."""
    stub, received = _recording_stub({"first": "F", "mid": "M"})
    group = _group("g", [
        _task("first", "a"),
        _task("mid", "b"),
        _task("last", 'prev={{previous_sibling.summary}} byid={{sibling("first").summary}}'),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    assert "prev=M" in received["last"]
    assert "byid=F" in received["last"]


@pytest.mark.asyncio
async def test_reference_to_not_yet_run_block_is_empty(ctx):
    """Referencing a block that runs LATER renders empty (not literal)."""
    stub, received = _recording_stub({"early": "E"})
    group = _group("g", [
        _task("early", 'later said {{sibling("late").summary}}'),
        _task("late", "runs after"),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    # 'late' hadn't completed when 'early' rendered → empty.
    assert received["early"] == "later said "


@pytest.mark.asyncio
async def test_loop_rerun_last_write_wins(ctx):
    """A block re-run in a loop overwrites its registry entry; a later
    reference sees the most recent result."""
    calls = {"n": 0}

    async def stub(block, project_root=None, project_id=None, run_id=None):
        if block.id == "body":
            calls["n"] += 1
            return Artifact(summary=f"iter-{calls['n']}", failed=False)
        return Artifact(summary=block.instructions, failed=False)

    # Repeat body runs 3x, then a tail task reads sibling("body").
    repeat = Block(block_type="repeat", id="r", name="r",
                   repeat_mode="count", repeat_count=3, repeat_propagate="none",
                   body=[_task("body", "step")])
    tail = _task("tail", '{{sibling("body").summary}}')
    group = _group("g", [repeat, tail])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    # Registry holds the LAST body iteration's summary.
    assert ctx.artifact_registry["body"].summary == "iter-3"


# ── The narrowed templating guard ──────────────────────────────
#
# _apply_templating_to_task used to return early whenever a run had no
# bindings, no variables, no context notes, no prior sibling AND an empty
# artifact registry — which is exactly the state of the FIRST block in a
# run.  Any sequence-scoped placeholder there was handed to the model as
# raw template text.  The guard now also renders when the instructions
# reference a prior block's result, while still leaving loop-scoped
# placeholders literal outside a loop.

@pytest.mark.asyncio
async def test_first_block_sibling_reference_does_not_leak_literal(ctx):
    """The regression: {{sibling(...)}} in the FIRST block of a run.

    Nothing has completed, so the registry is empty and the old guard
    skipped rendering entirely, leaking the template text into the
    model's instructions.
    """
    stub, received = _recording_stub({"first": "F"})
    group = _group("g", [
        _task("first", 'later={{sibling("second").summary}}'),
        _task("second", "runs after"),
    ])
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(group, ctx)
    assert received["first"] == "later="
    assert "{{" not in received["first"]


@pytest.mark.asyncio
async def test_lone_task_sibling_reference_renders_empty(ctx):
    """A single-block run: no sequence at all, still must not leak."""
    stub, received = _recording_stub({"only": "O"})
    lone = _task("only", 'ref={{sibling("nothing")}} end')
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(lone, ctx)
    assert received["only"] == "ref= end"


@pytest.mark.asyncio
async def test_loop_placeholder_still_literal_outside_a_loop(ctx):
    """The guard must stay narrow.

    {{index}} resolves to "0" against default bindings, so rendering it
    outside a loop would assert an iteration that never happened.  It
    must remain literal even now that sequence placeholders render.
    """
    stub, received = _recording_stub({"only": "O"})
    lone = _task("only", "plain {{index}} and {{item}}")
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(lone, ctx)
    assert received["only"] == "plain {{index}} and {{item}}"


@pytest.mark.asyncio
async def test_mixed_reference_renders_the_whole_string(ctx):
    """Documents a sharp edge: the render decision is per-STRING.

    Once any sequence placeholder opts a task into rendering, EVERY known
    placeholder in the same instructions resolves — so {{index}} beside a
    {{sibling(...)}} reference becomes "0" even with no loop active,
    whereas alone it would stay literal.

    Pinned rather than "fixed": making the choice per-placeholder would
    mean rendering a template twice under different rules, and the
    combination is an authoring mistake either way ({{index}} outside a
    loop is meaningless).  Recorded here so the behavior is discovered
    from a test rather than from a confusing run.
    """
    stub, received = _recording_stub({"only": "O"})
    lone = _task("only", 'sib={{sibling("x")}} idx={{index}}')
    with patch("app.agents.block_executor.execute_task_block", side_effect=stub):
        await execute_block(lone, ctx)
    assert received["only"] == "sib= idx=0"


def test_sequence_placeholder_matcher():
    """Unit-level: which placeholders opt a task into rendering."""
    from app.agents.block_executor import _references_sequence_placeholder as f
    assert f('{{sibling("a")}}')
    assert f("{{previous_sibling}}")
    assert f("{{ previous_sibling.summary }}")
    assert f("{{sibling('a').outputs.r.k}}")
    # Loop-scoped and var placeholders must NOT trigger rendering.
    assert not f("{{index}}")
    assert not f("{{item}}")
    assert not f("{{var.x}}")
    assert not f("{{previous.summary}}")
    assert not f("no placeholders")
    assert not f("")
    assert not f(None)
