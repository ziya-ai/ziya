"""
Mid-loop resume through the FULL executor, with real storage.

``test_resume_mid_loop.py`` covers the resolution rules and
``test_resume_mid_loop_execution.py`` the loop's iteration accounting —
but both stub ``_execute_sequence``, so neither exercises the thing most
likely to be wrong: the block-level resume gate and the iteration index
COOPERATING.  A real mid-loop resume passes through both.  ``resume_skipping``
is true from the root until the loop is reached, at which point the gate
clears itself and the iteration index takes over.

These drive ``execute_block`` on a Group(prep, loop) tree against a real
``TaskRunStorage``, stubbing only ``execute_task_block`` (the single
boundary where a model call would happen), and assert on what was
persisted.

The chained-resume tests pin a defect the unit tests could not see: a run
that is ITSELF a mid-loop resume records only the iterations it executed,
so its own first visible dot appeared unresumable.
"""

import pytest

import app.agents.block_executor as be
from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunBlockState, TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.utils.resume_targets import resolve_iteration_resume


def _artifact(summary, failed=False, objective_met=None):
    a = Artifact(summary=summary, created_at=0.0, failed=failed)
    if objective_met is not None:
        a.self_assessment = {"objective_met": objective_met}
    return a


@pytest.fixture
def storage(tmp_path):
    # Takes a Path: __init__ does ``project_dir / "task_runs"``.
    return TaskRunStorage(tmp_path)


@pytest.fixture
def stub_task(monkeypatch):
    """Replace the ONE symbol that would reach a model.

    ``execute_task_block`` is imported into block_executor's namespace at
    module load, so it must be patched THERE — patching
    ``task_executor.execute_task_block`` would leave the already-bound
    reference untouched and real calls would fire.

    Returns the list of rendered instruction strings, one per executed
    task.  Those strings carry whatever ``{{previous}}`` resolved to,
    which is how the replayed prefix is observed rather than assumed.
    """
    seen = []

    async def _fake(effective, **kwargs):
        seen.append(effective.instructions or "")
        return _artifact(f"fresh {len(seen)}", objective_met="false")

    monkeypatch.setattr(be, "execute_task_block", _fake)
    return seen


def _seed(storage, run_id, block):
    """Pre-populate block_states, as the launch endpoint does."""
    if block.id:
        storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type, status="queued",
        ))
    for child in block.body or []:
        _seed(storage, run_id, child)


def _tree(loop_type="repeat", count=5, propagate="last"):
    """Group(prep, loop(body)) — a block BEFORE the loop is the point.

    Without a preceding block the block-level gate never has anything to
    replay, and the interaction under test does not arise.
    """
    loop = Block(
        id="loop", block_type=loop_type,
        body=[Block(
            id="body", block_type="task",
            instructions="prev={{previous.summary}}",
        )],
    )
    if loop_type == "repeat":
        loop.repeat_mode = "count"
        loop.repeat_count = count
        loop.repeat_propagate = propagate
    else:
        loop.until_max = count
        loop.until_condition = ""
    return Block(id="root", block_type="group", body=[
        Block(id="prep", block_type="task", instructions="prep"),
        loop,
    ])


async def _run_resume(storage, stub_task, tree, start, replayed_summaries):
    """Execute a mid-loop resume and return the fresh run record."""
    run = storage.create(TaskRunCreate(card_id="c1"))
    _seed(storage, run.id, tree)
    ctx = ExecutionContext(
        run_id=run.id, storage=storage,
        # Block-level gate: skip until the loop.
        resume_from_block_id="loop",
        resume_skipping=True,
        resume_artifacts={"prep": _artifact("PREP-RECORDED")},
        # Iteration gate: start the loop at ``start``.
        resume_from_iteration=start,
        resume_iteration_artifacts={
            i: _artifact(s) for i, s in enumerate(replayed_summaries)
        },
    )
    await execute_block(tree, ctx)
    return storage.get(run.id), ctx


class TestBothGatesCooperate:
    @pytest.mark.asyncio
    async def test_block_before_the_loop_replays_and_loop_starts_at_index(
        self, storage, stub_task,
    ):
        fresh, _ = await _run_resume(
            storage, stub_task, _tree(count=5), 3, ["rep0", "rep1", "rep2"],
        )
        # prep replayed: not executed, but carries the recorded artifact.
        assert fresh.block_states["prep"].status == "skipped"
        assert fresh.block_states["prep"].artifact.summary == "PREP-RECORDED"
        # Only iterations 3 and 4 ran — two task calls, not seven.
        assert len(stub_task) == 2
        assert [s.index for s in fresh.block_states["loop"].iteration_summaries] \
            == [3, 4]

    @pytest.mark.asyncio
    async def test_first_executed_iteration_receives_the_replayed_previous(
        self, storage, stub_task,
    ):
        # The load-bearing assertion: iteration 3's rendered instructions
        # must contain iteration 2's RECORDED summary.  If the replay
        # prefix failed to thread, this reads "prev=" and the iteration
        # would have run against nothing while the run reported success.
        await _run_resume(
            storage, stub_task, _tree(count=5), 3, ["rep0", "rep1", "rep2"],
        )
        assert "prev=rep2" in stub_task[0]
        # And the chain continues from real output, not from the record.
        assert "prev=fresh 1" in stub_task[1]

    @pytest.mark.asyncio
    async def test_replayed_prep_is_visible_to_sibling_lookups(
        self, storage, stub_task,
    ):
        _, ctx = await _run_resume(
            storage, stub_task, _tree(count=4), 2, ["r0", "r1"],
        )
        # {{sibling("prep")}} must resolve to the replayed artifact — this
        # is what "prior deck state is preserved" means concretely.
        assert ctx.artifact_registry["prep"].summary == "PREP-RECORDED"

    @pytest.mark.asyncio
    async def test_loop_completes_rather_than_reporting_failure(
        self, storage, stub_task,
    ):
        fresh, _ = await _run_resume(
            storage, stub_task, _tree(count=5), 3, ["r0", "r1", "r2"],
        )
        assert fresh.block_states["loop"].status == "done"

    @pytest.mark.asyncio
    async def test_inner_body_block_stays_unpersisted(
        self, storage, stub_task,
    ):
        # Documents WHY iteration-level resume needed new plumbing:
        # _mark_block_status skips blocks inside an active loop iteration,
        # so the body block never leaves 'queued' and cannot itself be a
        # resume target.
        fresh, _ = await _run_resume(
            storage, stub_task, _tree(count=3), 1, ["r0"],
        )
        assert fresh.block_states["body"].status == "queued"


class TestUntilThroughFullExecutor:
    @pytest.mark.asyncio
    async def test_replayed_success_does_not_end_an_until_loop(
        self, storage, stub_task,
    ):
        # The false-success trap, through the real executor: Until's
        # layer 1 breaks on objective_met=true, so feeding it a replayed
        # iteration would exit at 0 having executed nothing while
        # reporting the goal met.
        run = storage.create(TaskRunCreate(card_id="c1"))
        tree = _tree(loop_type="until", count=5)
        _seed(storage, run.id, tree)
        replayed = {i: _artifact("identical", objective_met="true")
                    for i in range(3)}
        ctx = ExecutionContext(
            run_id=run.id, storage=storage,
            resume_from_block_id="loop", resume_skipping=True,
            resume_artifacts={"prep": _artifact("P")},
            resume_from_iteration=3,
            resume_iteration_artifacts=replayed,
        )
        await execute_block(tree, ctx)
        assert len(stub_task) == 2, (
            "the until loop exited without doing the work — a replayed "
            "self_assessment satisfied the exit condition"
        )
        fresh = storage.get(run.id)
        assert [s.index for s in fresh.block_states["loop"].iteration_summaries] \
            == [3, 4]


class TestChainedResume:
    """A resume OF a resume.

    The defect these pin: a mid-loop-resumed run persists only the
    iterations it executed, so its own earliest visible dot had no
    recorded predecessor and was refused — the feature worked exactly
    once per campaign.  The inherited artifacts are on the run record
    (``resume_iteration_artifacts``), so the fix is a lookup, not new
    storage.
    """

    def _card(self):
        return {"id": "root", "block_type": "group", "body": [
            {"id": "loop", "block_type": "repeat", "body": []},
        ]}

    def _executed_only(self):
        # What run B records after resuming run A at index 3.
        return [
            {"index": 3, "status": "passed", "has_artifact": True},
            {"index": 4, "status": "failed", "has_artifact": True},
        ]

    def test_without_inherited_the_first_visible_dot_is_refused(self):
        # The bug, stated: retrying the earliest dot the user can SEE.
        start, err = resolve_iteration_resume(
            self._card(), "loop", 3, self._executed_only(), "retry_iteration",
        )
        assert start is None
        assert "never recorded" in err

    def test_inherited_iterations_make_it_resumable(self):
        start, err = resolve_iteration_resume(
            self._card(), "loop", 3, self._executed_only(), "retry_iteration",
            inherited={0: {}, 1: {}, 2: {}},
        )
        assert err is None
        assert start == 3

    def test_an_inherited_index_is_itself_retryable(self):
        # The dots for inherited iterations are rendered too, so clicking
        # one must work rather than reporting it was never recorded.
        start, err = resolve_iteration_resume(
            self._card(), "loop", 2, self._executed_only(), "retry_iteration",
            inherited={0: {}, 1: {}, 2: {}},
        )
        assert err is None
        assert start == 2

    def test_continue_across_the_inherited_boundary(self):
        start, err = resolve_iteration_resume(
            self._card(), "loop", 2, self._executed_only(),
            "continue_iteration", inherited={0: {}, 1: {}, 2: {}},
        )
        assert err is None
        assert start == 3

    def test_inherited_bypasses_the_retention_check(self):
        # An inherited entry IS a replayable artifact — it was carried
        # onto the run for that purpose — so a has_artifact=False summary
        # for the same index must not veto it.
        sums = [{"index": 2, "status": "passed", "has_artifact": False},
                {"index": 3, "status": "passed", "has_artifact": True}]
        start, err = resolve_iteration_resume(
            self._card(), "loop", 3, sums, "retry_iteration",
            inherited={2: {}},
        )
        assert err is None
        assert start == 3

    def test_retention_refusal_still_applies_without_inheritance(self):
        # The guard must not be weakened for the non-chained case.
        sums = [{"index": 2, "status": "passed", "has_artifact": False},
                {"index": 3, "status": "passed", "has_artifact": True}]
        start, err = resolve_iteration_resume(
            self._card(), "loop", 3, sums, "retry_iteration",
        )
        assert start is None
        assert "not retained" in err

    def test_empty_inherited_behaves_exactly_as_before(self):
        # Regression guard: the common (non-chained) path is untouched.
        for inh in (None, {}):
            start, err = resolve_iteration_resume(
                self._card(), "loop", 3, self._executed_only(),
                "retry_iteration", inherited=inh,
            )
            assert start is None
            assert "never recorded" in err

    def test_string_keys_from_a_disk_read_are_accepted(self):
        # JSON object keys are strings, so a run read back off disk can
        # present {"2": {...}}; reading only int keys would silently lose
        # the inheritance and re-break the chain after a server restart.
        start, err = resolve_iteration_resume(
            self._card(), "loop", 3, self._executed_only(),
            "retry_iteration", inherited={"0": {}, "1": {}, "2": {}},
        )
        assert err is None
        assert start == 3

    def test_inheritance_from_a_different_loop_is_not_consulted(self):
        # Guarded at the endpoint (it only passes inherited when
        # resumed_from_block_id matches), because another loop's indices
        # would otherwise appear to satisfy this loop's predecessor
        # requirement and the resume would run against a foreign input.
        start, err = resolve_iteration_resume(
            self._card(), "loop", 3, self._executed_only(),
            "retry_iteration", inherited={},
        )
        assert start is None
