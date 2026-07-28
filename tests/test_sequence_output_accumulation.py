"""Tests for output/decision accumulation across an implicit sequence.

design/task-cards.md §Runtime semantics says a sequence's artifact is
"the last block's artifact".  That is right for ``summary`` (a return
value) but was wrong for ``outputs``: emit_artifact parts are declared
durable deliverables, and returning only the last sibling's silently
discarded every earlier stage's work.  Observed live: a Group root whose
stages emitted 4 artifacts each reported ``outputs=[]`` at run level
while the per-iteration endpoint correctly showed them, so the run
tile's artifact viewer rendered nothing.

Covers:
  - outputs accumulate across siblings, in order
  - decisions accumulate across siblings
  - summary / failed stay last-wins (the documented contract)
  - the on_failure="stop" path keeps outputs from the steps that DID run
  - nested sequences roll their outputs up through the parent
  - a Repeat body's per-iteration accumulation reaches the loop artifact
  - {{previous_sibling}} still sees only its immediate predecessor
  - empty / no-output sequences don't crash or invent parts
"""

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, ArtifactPart, Block


def _part(name: str) -> ArtifactPart:
    return ArtifactPart(part_type="text", name=name, text=f"body-of-{name}")


@pytest.fixture
def emitting_tasks(monkeypatch):
    """Task executor stand-in where each task emits one artifact part
    named after itself, plus one decision.

    A task whose name contains "FAIL" returns a failed artifact (still
    with its part, since a failing step's evidence is exactly what we
    want preserved).
    """
    calls = []

    async def _fake(block, **kwargs):
        name = block.name or block.id
        calls.append(name)
        return Artifact(
            summary=f"summary-{name}",
            decisions=[f"decision-{name}"],
            outputs=[_part(name)],
            failed=("FAIL" in name),
            created_at=0.0,
        )

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    return calls


@pytest.fixture
def silent_tasks(monkeypatch):
    """Task executor stand-in that emits no outputs and no decisions."""
    async def _fake(block, **kwargs):
        return Artifact(summary=f"summary-{block.name}", created_at=0.0)

    monkeypatch.setattr(bx, "execute_task_block", _fake)


def _task(id_, name, instructions="do it"):
    return Block(block_type="task", id=id_, name=name, instructions=instructions)


def _ctx():
    # storage=None short-circuits cancel/persistence; run_id="" disables relay.
    return ExecutionContext(run_id="")


def _names(artifact):
    return [p.name for p in (artifact.outputs or [])]


# ── Accumulation ──────────────────────────────────────────────

class TestSequenceAccumulation:
    async def test_outputs_accumulate_in_order(self, emitting_tasks):
        grp = Block(block_type="group", id="g", body=[
            _task("t1", "alpha"),
            _task("t2", "beta"),
            _task("t3", "gamma"),
        ])
        result = await execute_block(grp, _ctx())
        assert _names(result) == ["alpha", "beta", "gamma"], (
            "every sibling's emitted parts must survive; returning only "
            "the last artifact silently discards earlier deliverables"
        )

    async def test_decisions_accumulate(self, emitting_tasks):
        grp = Block(block_type="group", id="g", body=[
            _task("t1", "alpha"),
            _task("t2", "beta"),
        ])
        result = await execute_block(grp, _ctx())
        assert result.decisions == ["decision-alpha", "decision-beta"]

    async def test_summary_stays_last_wins(self, emitting_tasks):
        """The documented contract for summary is unchanged."""
        grp = Block(block_type="group", id="g", body=[
            _task("t1", "alpha"),
            _task("t2", "omega"),
        ])
        result = await execute_block(grp, _ctx())
        assert result.summary == "summary-omega"

    async def test_failed_flag_stays_last_wins(self, emitting_tasks):
        """A failing early step under the default continue policy does
        not mark the sequence failed — that is existing behaviour and
        accumulation must not change it."""
        grp = Block(block_type="group", id="g", body=[
            _task("t1", "FAIL-early"),
            _task("t2", "ok-later"),
        ])
        result = await execute_block(grp, _ctx())
        assert result.failed is False
        # ...but the failing step's evidence is still preserved.
        assert "FAIL-early" in _names(result)

    async def test_no_outputs_yields_empty_list(self, silent_tasks):
        grp = Block(block_type="group", id="g", body=[
            _task("t1", "alpha"), _task("t2", "beta"),
        ])
        result = await execute_block(grp, _ctx())
        assert result.outputs == []

    async def test_empty_body_is_safe(self, silent_tasks):
        grp = Block(block_type="group", id="g", body=[])
        result = await execute_block(grp, _ctx())
        assert result.outputs == []


# ── on_failure="stop" keeps completed work ────────────────────

class TestStopPathPreservesOutputs:
    async def test_stop_keeps_outputs_of_steps_that_ran(self, emitting_tasks):
        grp = Block(block_type="group", id="g", on_failure="stop", body=[
            _task("t1", "ran-first"),
            _task("t2", "FAIL-mid"),
            _task("t3", "never-ran"),
        ])
        result = await execute_block(grp, _ctx())
        assert emitting_tasks == ["ran-first", "FAIL-mid"]
        # The completed step's deliverable must not be lost just because
        # a later step failed — that work is real and already done.
        assert _names(result) == ["ran-first", "FAIL-mid"]
        assert "never-ran" not in _names(result)

    async def test_stop_still_records_skip_note_and_failure(self, emitting_tasks):
        grp = Block(block_type="group", id="g", on_failure="stop", body=[
            _task("t1", "ran-first"),
            _task("t2", "FAIL-mid"),
            _task("t3", "never-ran"),
        ])
        result = await execute_block(grp, _ctx())
        assert result.failed
        assert any("skipped (on_failure=stop)" in d for d in result.decisions)
        # Accumulated decisions are preserved alongside the skip note —
        # the stop path's model_copy must not clobber them.
        assert "decision-ran-first" in result.decisions
        assert "decision-FAIL-mid" in result.decisions


# ── Nesting and loops ─────────────────────────────────────────

class TestNestedRollup:
    async def test_nested_sequence_rolls_up(self, emitting_tasks):
        inner = Block(block_type="group", id="inner", body=[
            _task("t2", "inner-a"),
            _task("t3", "inner-b"),
        ])
        outer = Block(block_type="group", id="outer", body=[
            _task("t1", "outer-first"),
            inner,
        ])
        result = await execute_block(outer, _ctx())
        assert _names(result) == ["outer-first", "inner-a", "inner-b"]

    async def test_repeat_body_accumulation_reaches_loop_artifact(
        self, emitting_tasks,
    ):
        """A Repeat already concatenates per-iteration outputs; with the
        sequence fix, each iteration contributes ALL its body's parts
        rather than just the last block's."""
        rpt = Block(
            block_type="repeat", id="r", repeat_mode="count", repeat_count=2,
            body=[_task("t1", "step-a"), _task("t2", "step-b")],
        )
        result = await execute_block(rpt, _ctx())
        # 2 iterations x 2 steps = 4 parts.
        assert _names(result) == [
            "step-a", "step-b", "step-a", "step-b",
        ]


# ── Propagation is deliberately NOT widened ───────────────────

class TestPropagationUnchanged:
    async def test_previous_sibling_sees_only_immediate_predecessor(
        self, monkeypatch,
    ):
        """{{previous_sibling}} reads ctx.sibling_stack[-1], which holds
        the RAW per-child artifact set before the fold.  A downstream
        sibling must therefore see only its immediate predecessor's
        summary, not the accumulation — propagation semantics are
        separately specified and must not silently widen."""
        seen = []

        async def _fake(block, **kwargs):
            seen.append(block.instructions or "")
            name = block.name or block.id
            return Artifact(
                summary=f"S-{name}", outputs=[_part(name)], created_at=0.0,
            )

        monkeypatch.setattr(bx, "execute_task_block", _fake)
        grp = Block(block_type="group", id="g", body=[
            _task("t1", "one", "first"),
            _task("t2", "two", "second"),
            _task("t3", "three", "prev={{previous_sibling.summary}}"),
        ])
        await execute_block(grp, _ctx())
        # Third task's rendered instructions reference ONLY task two.
        assert "S-two" in seen[2]
        assert "S-one" not in seen[2]
