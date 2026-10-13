"""``required: false`` on a container's child — a lane allowed to fail.

A Parallel reported failed whenever ANY child failed, and under
``Group(stop)`` that halted the run, so a Parallel of {1A baseline, 1B
known-issues} could not say "1A must pass, 1B may not".  Per-child
``required`` composes where a container-level knob could not: a Parallel
of eleven lanes can mark all eleven optional while a Group marks its
verification stage optional and its report stage required.  Default true
preserves current semantics for every existing card.

The failure is never silent: the container's ``decisions`` name the lane.

Fixture pattern from test_block_executor_failure_policy_and_foreach.py: a
task whose name contains "FAIL" returns a failed artifact.
"""

import pytest

from app.agents import block_executor as bx
from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block


@pytest.fixture
def fake_tasks(monkeypatch):
    calls = []

    async def _fake(block, **kwargs):
        calls.append(block.name)
        failed = "FAIL" in (block.name or "")
        return Artifact(summary=block.instructions or block.name or "",
                        failed=failed, signature="boom" if failed else None,
                        created_at=0.0)

    monkeypatch.setattr(bx, "execute_task_block", _fake)
    return calls


def _task(id_, name, required=None):
    kw = {} if required is None else {"required": required}
    return Block(block_type="task", id=id_, name=name, instructions=name, **kw)


def _ctx():
    return ExecutionContext(run_id="")


def test_required_is_a_declared_field_not_an_extra():
    """Seam: the executor reads ``required`` off the block, so it must be a
    declared model field with default True — not something that survives
    only because Block allows extras (and would be dropped by any code
    path that re-validates against the declared schema)."""
    assert "required" in Block.model_fields
    assert Block.model_fields["required"].default is True
    assert Block(block_type="task", id="t", name="t").required is True


# ── Parallel ────────────────────────────────────────────────────

async def test_parallel_default_still_fails_on_any_child_failure(fake_tasks):
    """Negative control: unmarked children keep the pre-field semantics."""
    par = Block(block_type="parallel", id="p", body=[
        _task("a", "ok-lane"), _task("b", "FAIL-lane"),
    ])
    art = await execute_block(par, _ctx())
    assert art.failed
    assert not any("optional" in d for d in art.decisions)


async def test_parallel_optional_child_failure_is_recorded_not_propagated(fake_tasks):
    par = Block(block_type="parallel", id="p", body=[
        _task("a", "baseline"), _task("b", "FAIL-known-issues", required=False),
    ])
    art = await execute_block(par, _ctx())
    assert not art.failed
    assert sorted(fake_tasks) == ["FAIL-known-issues", "baseline"], "both lanes ran"
    [d] = [d for d in art.decisions if d.startswith("optional child")]
    assert "FAIL-known-issues" in d and "required=false" in d and "boom" in d
    # The lane's own stage evidence still says failed — only propagation changes.
    assert [s["status"] for s in art.stages] == ["passed", "failed"]


async def test_parallel_required_child_failure_still_fails_beside_optional_one(fake_tasks):
    par = Block(block_type="parallel", id="p", body=[
        _task("a", "FAIL-baseline"), _task("b", "FAIL-known-issues", required=False),
    ])
    art = await execute_block(par, _ctx())
    assert art.failed


async def test_parallel_optional_child_that_raises_is_recorded_not_propagated(fake_tasks, monkeypatch):
    async def _raising(block, **kwargs):
        if "RAISE" in block.name:
            raise RuntimeError("lane blew up")
        return Artifact(summary="ok", created_at=0.0)
    monkeypatch.setattr(bx, "execute_task_block", _raising)
    par = Block(block_type="parallel", id="p", body=[
        _task("a", "baseline"), _task("b", "RAISE-lane", required=False),
    ])
    art = await execute_block(par, _ctx())
    assert not art.failed
    assert any("optional child" in d and "lane blew up" in d for d in art.decisions)


# ── Sequence (Group) ────────────────────────────────────────────

async def test_group_stop_does_not_halt_on_optional_child_failure(fake_tasks):
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "verify", required=False).model_copy(update={"name": "FAIL-verify"}),
        _task("t2", "report"),
    ])
    art = await execute_block(grp, _ctx())
    assert fake_tasks == ["FAIL-verify", "report"], "stop must not trip on an optional lane"
    assert not art.failed
    assert any("optional step 1/2" in d and "required=false" in d for d in art.decisions)
    assert not any("skipped" in d for d in art.decisions)


async def test_group_stop_still_halts_on_required_child_failure(fake_tasks):
    """Negative control for the sequence path."""
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        _task("t1", "FAIL-verify"), _task("t2", "report"),
    ])
    art = await execute_block(grp, _ctx())
    assert fake_tasks == ["FAIL-verify"]
    assert art.failed


async def test_group_whose_last_child_is_optional_and_fails_is_not_failed(fake_tasks):
    """last-wins for the sequence artifact, except an optional last child's
    failure is not the sequence's failure."""
    grp = Block(block_type="group", id="g", body=[
        _task("t1", "report"), _task("t2", "FAIL-cleanup", required=False),
    ])
    art = await execute_block(grp, _ctx())
    assert not art.failed and art.signature is None
    # The fake echoes instructions, which the sequence prefixes with the
    # previous step's result; the tail is the last child's own text.
    assert art.summary.endswith("FAIL-cleanup"), "summary is still last-wins"
    assert any("optional step 2/2" in d for d in art.decisions)


async def test_parallel_of_optional_lanes_inside_group_stop_does_not_halt_the_group(fake_tasks):
    """The shape from the field report: Parallel of lanes some of which may
    fail, inside a dependent Group(stop) whose report stage must still run."""
    grp = Block(block_type="group", id="g", on_failure="stop", body=[
        Block(block_type="parallel", id="p", body=[
            _task("a", "1A-baseline"),
            _task("b", "FAIL-1B-known-issues", required=False),
        ]),
        _task("r", "report"),
    ])
    art = await execute_block(grp, _ctx())
    assert "report" in fake_tasks
    assert not art.failed
