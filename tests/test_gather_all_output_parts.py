"""
Gathering EVERY iteration's named output part, not just the last.

The defect this pins, verified against the real executor before any fix:
a parallel Repeat whose iterations each emit a part under the same name
(the natural shape of a fan-out — one auditor per subsystem, all emitting
``audit``) accumulates all N parts onto the loop's artifact correctly, but
``find_output_part`` is deliberately LAST-WINS, so every template
reference resolves to the final iteration alone.  A 3-wide fan-out
rendered ``gamma`` and the other two workers' findings were unreachable.

Last-wins is right for its original purpose: a single task that emits a
part, notices a problem and re-emits under the same name means the
correction.  It is wrong across iterations, where each part belongs to a
different worker and none supersedes another.  So the fix is a separate
plural accessor rather than a change to the existing singular one —
changing last-wins would break the supersession semantics the singular
form exists to provide.

These tests assert on ORDER as well as membership.  Order is the property
that makes a gathered list usable: an author correlating the Nth result
with the Nth item of the roster that produced it needs iteration order,
and ``asyncio.gather`` preserves dispatch order even though completion
order is arbitrary.  A fix that gathered into a set, or into completion
order, would satisfy a membership-only test while being useless for the
case that motivated it.
"""

import asyncio

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, execute_block
from app.agents.task_templating import (
    IterationBindings,
    find_output_part,
    find_all_output_parts,
    render,
)
from app.models.task_card import Artifact, ArtifactPart, Block


def _part(name, payload, iteration=None):
    kw = {"part_type": "data", "data": payload, "name": name}
    if iteration is not None:
        kw["iteration"] = iteration
    return ArtifactPart(**kw)


def _ctx():
    return ExecutionContext(run_id="")


@pytest.fixture
def fanout_tasks(monkeypatch):
    """Each iteration emits one data part named 'audit' carrying its item."""
    async def _fake(block, **kwargs):
        item = (block.instructions or "").strip().split()[-1]
        return Artifact(
            summary=f"audited {item}",
            outputs=[_part("audit", {"subsystem": item})],
            created_at=0.0,
        )

    monkeypatch.setattr(bx, "execute_task_block", _fake)


def _fanout(parallel: bool, items='["alpha","beta","gamma"]'):
    return Block(
        block_type="repeat", id="r", name="Fan out",
        repeat_mode="for_each", repeat_for_each_source=items,
        repeat_parallel=parallel,
        body=[Block(block_type="task", id="t", name="w",
                    instructions="audit {{item}}")],
    )


# ── the accessor itself ────────────────────────────────────────

class TestFindAllOutputParts:
    def test_returns_every_part_under_the_name(self):
        art = Artifact(outputs=[
            _part("audit", {"n": 1}), _part("audit", {"n": 2}),
            _part("audit", {"n": 3}),
        ])
        got = find_all_output_parts(art, "audit")
        assert [p.data["n"] for p in got] == [1, 2, 3]

    def test_preserves_list_order(self):
        """Order is the whole point; a set or dict would lose it."""
        art = Artifact(outputs=[
            _part("audit", {"n": 3}), _part("audit", {"n": 1}),
            _part("audit", {"n": 2}),
        ])
        assert [p.data["n"] for p in find_all_output_parts(art, "audit")] == [3, 1, 2]

    def test_ignores_parts_with_other_names(self):
        art = Artifact(outputs=[
            _part("audit", {"n": 1}), _part("notes", {"n": 99}),
            _part("audit", {"n": 2}),
        ])
        assert [p.data["n"] for p in find_all_output_parts(art, "audit")] == [1, 2]

    def test_absent_name_yields_empty_list_not_none(self):
        """Empty list, so a caller can iterate without a None check."""
        art = Artifact(outputs=[_part("other", {"n": 1})])
        assert find_all_output_parts(art, "audit") == []

    def test_none_artifact_yields_empty_list(self):
        assert find_all_output_parts(None, "audit") == []

    def test_empty_name_yields_empty_list(self):
        art = Artifact(outputs=[_part("audit", {"n": 1})])
        assert find_all_output_parts(art, "") == []

    def test_singular_accessor_is_unchanged(self):
        """The supersession semantics of the singular form must survive.

        A task that re-emits under one name means the correction, and
        that is exactly the case the singular accessor serves.
        """
        art = Artifact(outputs=[
            _part("audit", {"n": 1}), _part("audit", {"n": 2}),
        ])
        assert find_output_part(art, "audit").data["n"] == 2


# ── templating surface ─────────────────────────────────────────

class TestOutputsAllTemplating:
    def test_renders_every_payload_as_a_json_array(self):
        art = Artifact(outputs=[
            _part("audit", {"subsystem": "alpha"}),
            _part("audit", {"subsystem": "beta"}),
        ])
        out = render("{{previous_sibling.outputs_all.audit}}",
                     IterationBindings(previous_sibling=art))
        assert "alpha" in out and "beta" in out
        assert out.strip().startswith("[")

    def test_dotted_path_projects_one_field_across_iterations(self):
        """The shape that makes a gathered fan-out drive a later loop."""
        art = Artifact(outputs=[
            _part("audit", {"subsystem": "alpha"}),
            _part("audit", {"subsystem": "beta"}),
        ])
        out = render("{{previous_sibling.outputs_all.audit.subsystem}}",
                     IterationBindings(previous_sibling=art))
        assert out == '["alpha", "beta"]'

    def test_absent_part_renders_empty_array_not_blank(self):
        """An empty ARRAY keeps a downstream for_each source parseable.

        Rendering "" would make the source unresolvable and fail the
        block; "[]" is honestly "nothing to iterate".
        """
        art = Artifact(outputs=[_part("other", {"x": 1})])
        out = render("{{previous_sibling.outputs_all.audit}}",
                     IterationBindings(previous_sibling=art))
        assert out == "[]"

    def test_sibling_by_id_form_works(self):
        art = Artifact(outputs=[_part("audit", {"subsystem": "alpha"})])
        out = render('{{sibling("fan").outputs_all.audit.subsystem}}',
                     IterationBindings(sibling_artifacts={"fan": art}))
        assert out == '["alpha"]'

    def test_bare_outputs_all_stays_literal(self):
        """Matches the bare-{{...outputs}} rule: name a part or nothing."""
        art = Artifact(outputs=[_part("audit", {"x": 1})])
        tpl = "{{previous_sibling.outputs_all}}"
        assert render(tpl, IterationBindings(previous_sibling=art)) == tpl


# ── end to end through the executor ────────────────────────────

class TestGatherThroughTheExecutor:
    async def test_parallel_fanout_exposes_all_iterations(self, fanout_tasks):
        """The regression: this rendered only 'gamma' before the fix."""
        art = await execute_block(_fanout(parallel=True), _ctx())
        got = find_all_output_parts(art, "audit")
        assert [p.data["subsystem"] for p in got] == ["alpha", "beta", "gamma"]

    async def test_parallel_order_is_dispatch_order(self, fanout_tasks):
        """Completion order is arbitrary; gather preserves dispatch order.

        Pinned because an author correlating the Nth result with the Nth
        roster item depends on it, and nothing else states it.
        """
        art = await execute_block(_fanout(parallel=True), _ctx())
        rendered = render(
            "{{previous_sibling.outputs_all.audit.subsystem}}",
            IterationBindings(previous_sibling=art),
        )
        assert rendered == '["alpha", "beta", "gamma"]'

    async def test_serial_fanout_exposes_all_iterations(self, fanout_tasks):
        art = await execute_block(_fanout(parallel=False), _ctx())
        got = find_all_output_parts(art, "audit")
        assert [p.data["subsystem"] for p in got] == ["alpha", "beta", "gamma"]

    async def test_singular_reference_still_resolves_to_the_last(
        self, fanout_tasks,
    ):
        """Existing cards keep working; this is additive."""
        art = await execute_block(_fanout(parallel=True), _ctx())
        assert find_output_part(art, "audit").data["subsystem"] == "gamma"

    async def test_gathered_list_can_drive_a_downstream_for_each(
        self, fanout_tasks, monkeypatch,
    ):
        """The end the whole feature serves: fan out, gather, fan out again.

        Before this, the second loop could only ever see the last worker's
        output, so a two-stage decomposition needed a hand-written merge
        task reading a file blackboard.
        """
        stage1 = await execute_block(_fanout(parallel=True), _ctx())

        seen = []

        async def _second(block, **kwargs):
            seen.append((block.instructions or "").strip())
            return Artifact(summary="ok", created_at=0.0)

        monkeypatch.setattr(bx, "execute_task_block", _second)

        second = Block(
            block_type="repeat", id="r2", repeat_mode="for_each",
            repeat_for_each_source=(
                "{{previous_sibling.outputs_all.audit.subsystem}}"
            ),
            body=[Block(block_type="task", id="t2", name="deep",
                        instructions="deep dive {{item}}")],
        )
        ctx = _ctx()
        ctx.sibling_stack.append(stage1)
        await execute_block(second, ctx)
        # Substring, not equality: the executor prepends an auto-generated
        # iteration-context preamble ("Iteration number", "Current item")
        # to every templated task, so exact equality asserts against the
        # harness rather than the feature.  Matches the convention in
        # test_block_executor_failure_policy_and_foreach.py.
        assert len(seen) == 3, "every gathered item must drive one iteration"
        for expected, actual in zip(
            ["deep dive alpha", "deep dive beta", "deep dive gamma"], seen,
        ):
            assert expected in actual
