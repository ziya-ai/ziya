"""A CHAIN of resumes must not re-run work it is already holding.

Every existing test in this area covers the FIRST resume: a source run
that executed its own iterations, so its ``iteration_summaries`` describe
them and ``parallel_replay_indices`` can select from that record.  That
path worked.  The one nobody asserted is the SECOND resume, and it is the
common one -- a long fan-out that holds on infrastructure typically holds
more than once.

The defect, measured on a real 60-wide fan-out (run 9099930d, attempt 4
of a six-phase orchestrator):

  * attempt 3 held; the resume banked 50 passed iterations and copied
    their artifacts onto attempt 4.  50 artifact files landed on disk.
  * ``seed_replayed_iterations`` was then supposed to install matching
    ``replayed=True`` summaries -- and returned early, because the loop
    lives inside a CALLED card and launch-time seeding walks only the
    CALLER's tree, so there was no ``block_states`` entry to write into.
    The artifact copies need no state, so they landed anyway.
  * attempt 4 therefore held 50 artifacts and 0 records that it held
    them.  Resuming it banked 8 of 60 and re-ran 52 -- in order to redo
    the 2 iterations that had actually failed.

The proof the seeder never ran, taken off the real record, is that the
surviving summaries are in *insertion* order (36, 50, 56, 57, 34, 33, 55,
20, 59, 58) rather than sorted: ``seed_replayed_iterations`` sorts the
merged list, so a sorted strip is its fingerprint and an unsorted one is
its absence.

Three seams, each of which was independently wrong, and all three of
which must hold or the chain leaks:

  A. the endpoint must bank what the run INHERITED, not only what it
     executed  (app/api/task_runs.py, resume_run_from_block)
  B. the launch path must be able to seed a prefix for a block whose
     state does not exist yet, WITHOUT inventing state for a block no
     card contains  (app/storage/task_runs.py, seed_replayed_iterations)
  C. the Call must not wipe that prefix when it seeds the callee subtree
     (app/agents/block_executor.py, _seed_callee_block_states)

Seam A is the one that repairs runs already in this state; B and C stop
new ones entering it.  ``TestTheChainHolds`` is the test that would have
caught the original defect: A, B and C each passed in isolation while the
feature was broken end to end.
"""

import json
import time
from typing import Any, Dict, List, Set
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import (
    IterationSummary, TaskRunBlockState, TaskRunCreate,
)
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage

CHAT_ID = "chat-inherited-banking"
WIDTH = 20
LOOP_ID = "b-cf96c4e2"
OTHER_LOOP_ID = "b-otherloop"
CALL_ID = "call-p1"

# Carried onto this attempt by the PREVIOUS resume: artifacts on disk,
# no summaries.  Indices 0-13.
INHERITED: Set[int] = set(range(0, 14))
# Executed by THIS attempt: four passes and two failures.
EXECUTED_PASSED: Set[int] = {14, 15, 16, 17}
EXECUTED_FAILED: Set[int] = {18, 19}


# ── fixture tree ───────────────────────────────────────────────────

def _callee_root() -> Block:
    """CL1: recon, the auditor fan-out, a second loop, then a merge.

    The second loop exists only so a test can prove the inherited set is
    scoped to the loop being resumed rather than applied to any loop.
    """
    return Block(block_type="group", id="b-cl1-root", name="CL1", body=[
        Block(block_type="task", id="b-recon", name="Stage 1: Recon",
              instructions="map subsystems"),
        Block(
            block_type="repeat", id=LOOP_ID,
            name="Stage 2: Parallel subsystem auditors",
            repeat_mode="count", repeat_count=WIDTH,
            repeat_parallel=True, repeat_propagate="none",
            body=[Block(block_type="task", id="b-auditor",
                        name="Audit subsystem", instructions="audit")],
        ),
        Block(
            block_type="repeat", id=OTHER_LOOP_ID, name="Stage 2b: Others",
            repeat_mode="count", repeat_count=WIDTH,
            repeat_parallel=True, repeat_propagate="none",
            body=[Block(block_type="task", id="b-other",
                        name="Other", instructions="other")],
        ),
        Block(block_type="task", id="b-merge", name="Stage 3: Merge",
              instructions="merge"),
    ])


def _caller_root() -> Block:
    """CL0: a State block, then six Calls with EMPTY bodies."""
    return Block(block_type="group", id="root", name="CL0", body=[
        Block(block_type="state", id="b-params", name="Study parameters",
              state_context="Code is truth."),
        *[
            Block(block_type="call", id=f"call-p{n}", name=f"Phase {n}",
                  call_target=f"CL{n}", call_target_kind="card")
            for n in range(1, 7)
        ],
    ])


@pytest.fixture
def env(tmp_path):
    """A run in the exact post-resume state that loses the prefix.

    ``resume_iteration_artifacts`` names 14 inherited indices and their
    artifact files are present, but ``iteration_summaries`` holds only
    the six this attempt executed -- which is what a dropped
    ``seed_replayed_iterations`` leaves behind.
    """
    home = tmp_path / ".ziya"
    pid = "proj-inherited"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Inherited Banking", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))

    caller, callee = _caller_root(), _callee_root()
    card = TaskCardStorage(pdir).create(
        TaskCardCreate(name="CL0", root=caller))

    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(
        card_id=card.id,
        source_conversation_id=CHAT_ID,
        # This run is itself attempt 2 of a lineage, resumed at the loop.
        attempt=2,
        resume_kind="retry_from",
        resumed_from_block_id=LOOP_ID,
        resume_iteration_artifacts={
            i: Artifact(summary=f"inherited {i}", created_at=time.time())
            for i in sorted(INHERITED)
        },
    ))
    runs.set_card_snapshot(src.id, {
        "name": "CL0", "description": "", "root": caller.model_dump(),
    })
    runs.record_call(src.id, CALL_ID, {
        "call_block_id": CALL_ID, "target": "CL1", "kind": "card",
        "key": "card:cl1", "root": callee.model_dump(),
    })

    # Artifact files for EVERY index: the inherited ones were copied onto
    # this run at launch (that half of the resume path works), the
    # executed ones were written as they ran.
    for i in range(WIDTH):
        runs.write_iteration_artifact(src.id, LOOP_ID, i, Artifact(
            summary=f"subsystem {i} audited", created_at=time.time(),
            failed=i in EXECUTED_FAILED,
        ))

    # ...but summaries ONLY for what this attempt executed.  The 14
    # inherited records are the ones the dropped seeding lost.
    summaries: List[IterationSummary] = [
        IterationSummary(index=i, status="passed", has_artifact=True,
                         duration_ms=120_000, tokens=2_000_000)
        for i in sorted(EXECUTED_PASSED)
    ] + [
        IterationSummary(index=i, status="failed", has_artifact=True,
                         signature="0f42e965acb2",
                         duration_ms=147_000, tokens=2_200_000)
        for i in sorted(EXECUTED_FAILED)
    ]
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=LOOP_ID, block_type="repeat", status="failed",
        iteration_summaries=summaries,
    ))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id="b-recon", block_type="task", status="done",
        artifact=Artifact(summary="recon done", created_at=time.time()),
    ))
    runs.update_status(src.id, "partial")
    return home, pid, pdir, card.id, src.id


@pytest.fixture
def client(env):
    """The real route, with the LAUNCH captured instead of performed."""
    home, pid, pdir, card_id, src_id = env
    captured: Dict[str, Any] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return TaskRunStorage(pdir).create(TaskRunCreate(card_id=card_id))

    with patch("app.api.task_runs.get_ziya_home", return_value=home), \
         patch("app.api.task_runs.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards._launch_run_for_card", new=_capture):
        from app.api.task_runs import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), pid, src_id, captured, pdir


def _resume(tc, pid, run_id, block_id, mode="retry"):
    return tc.post(
        f"/api/v1/projects/{pid}/task-runs/{run_id}"
        f"/resume-from/{block_id}?mode={mode}"
    )


def _banked(captured) -> Set[int]:
    return {int(k) for k in (captured["resume_iteration_artifacts"] or {})}


# ── the fixture is the real shape ──────────────────────────────────

class TestTheFixtureReproducesTheRealState:
    """Without these, every assertion below could pass vacuously."""

    def test_the_loop_is_not_in_the_callers_snapshot(self):
        """The asymmetry that causes the dropped prefix in the first place."""
        from app.utils.resume_targets import find_block
        assert find_block(_caller_root().model_dump(), LOOP_ID) is None, (
            "fixture is wrong: the loop must live in the CALLEE tree, or "
            "this suite is not testing the shape that breaks"
        )

    def test_the_inherited_indices_have_no_summaries(self, env):
        """The defect itself, as a precondition."""
        *_, pdir, _card, src = env
        state = TaskRunStorage(pdir).get(src).block_states[LOOP_ID]
        recorded = {s.index for s in state.iteration_summaries}
        assert not (recorded & INHERITED), (
            "fixture is wrong: inherited indices must have NO summaries; "
            "that absence is the bug being tested"
        )
        assert recorded == EXECUTED_PASSED | EXECUTED_FAILED

    def test_the_inherited_artifacts_are_on_disk(self, env):
        """The other half: artifacts present, records absent."""
        *_, pdir, _card, src = env
        runs = TaskRunStorage(pdir)
        for i in sorted(INHERITED):
            assert runs.read_iteration_artifact(src, LOOP_ID, i) is not None, (
                f"fixture is wrong: inherited artifact {i} must exist on "
                f"disk, since it is what the run is holding"
            )

    def test_the_run_records_what_it_inherited(self, env):
        *_, pdir, _card, src = env
        run = TaskRunStorage(pdir).get(src)
        assert {int(k) for k in run.resume_iteration_artifacts} == INHERITED
        assert run.resumed_from_block_id == LOOP_ID


# ── Seam A: the endpoint banks inherited work ──────────────────────

class TestInheritedIterationsAreBanked:
    """The measured cost: 52 of 60 re-run to redo 2."""

    def test_the_request_succeeds(self, client):
        tc, pid, src, _cap, _pdir = client
        res = _resume(tc, pid, src, LOOP_ID)
        assert res.status_code == 200, res.text

    def test_every_finished_iteration_is_banked(self, client):
        """18 of 20 banked, not 4 of 20."""
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        expected = INHERITED | EXECUTED_PASSED
        got = _banked(cap)
        assert got == expected, (
            f"banked {len(got)} of {WIDTH}; expected {len(expected)}. "
            f"Missing {sorted(expected - got)} -- these are iterations "
            f"whose artifacts the run is already holding, so re-running "
            f"them buys nothing and costs a full frontier-tier agent each."
        )

    def test_only_the_genuinely_unfinished_iterations_re_run(self, client):
        """The positive half: the resume is not a no-op.

        Paired with the assertion above deliberately.  A change that
        banked EVERYTHING would satisfy 'nothing is re-run needlessly'
        while making the resume report success without redoing the
        failures -- which is the worse of the two bugs.
        """
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        re_run = set(range(WIDTH)) - _banked(cap)
        assert re_run == EXECUTED_FAILED, (
            f"expected exactly the failed iterations {sorted(EXECUTED_FAILED)} "
            f"to re-run; got {sorted(re_run)}"
        )

    def test_inherited_records_are_marked_replayed(self, client):
        """``replayed=True`` keeps them out of every progress aggregate.

        Banking without the flag would credit the new attempt with work a
        previous one performed -- the same lie in the other direction,
        and the reason run_outcome excludes them.
        """
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        by_index = {s.index: s for s in cap["resume_iteration_summaries"]}
        for i in sorted(INHERITED):
            assert i in by_index, f"inherited {i} has no display record"
            assert by_index[i].replayed is True, (
                f"inherited iteration {i} is not marked replayed; it would "
                f"be counted as this attempt's own progress"
            )
            assert by_index[i].status == "passed"

    def test_selection_alone_would_bank_only_four(self, client):
        """Non-vacuity: proves the endpoint's fallback is load-bearing.

        ``parallel_replay_indices`` is what the endpoint banked BEFORE the
        inherited-artifact fallback existed.  Run against this same
        record it returns 4 of 20, so the 18 asserted above cannot come
        from selection -- they can only come from the fallback.  Without
        this contrast, a fixture that happened to carry summaries for the
        inherited indices would make the assertions above pass against
        unpatched code, certifying the bug.
        """
        from app.utils.resume_targets import parallel_replay_indices

        tc, pid, src, cap, pdir = client
        runs = TaskRunStorage(pdir)
        run = runs.get(src)
        state = run.block_states[LOOP_ID]
        selection_only = parallel_replay_indices(
            (run.card_snapshot or {}).get("root"), LOOP_ID,
            [s.model_dump() for s in state.iteration_summaries],
            run.call_snapshots,
        )
        assert set(selection_only or []) == EXECUTED_PASSED, (
            "the fixture's own record already yields the inherited indices; "
            "the banking assertions above would then pass without the fix"
        )

        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        assert len(_banked(cap)) == len(INHERITED | EXECUTED_PASSED)
        assert len(_banked(cap)) > len(selection_only or []), (
            "the endpoint banked no more than bare selection; the "
            "inherited-artifact fallback is not running"
        )

    def test_a_display_record_exists_for_every_banked_index(self, client):
        """A banked artifact with no summary is how this defect recurs.

        The dot strip opens ``/iterations/{block}/{index}``, and the NEXT
        resume selects from these summaries -- so an artifact banked
        without a matching record re-creates exactly the state this file
        exists to prevent.
        """
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        assert {s.index for s in cap["resume_iteration_summaries"]} \
            == _banked(cap), (
                "banked artifacts and display records disagree; the "
                "difference is silently-unrecorded banked work"
            )


class TestTheInheritedSetIsNotTrustedBlindly:
    """Three ways banking the inherited set could go wrong."""

    def test_an_index_this_attempt_re_ran_and_failed_is_not_banked(
            self, env, client):
        """The exclusion that stops this undoing the resume.

        Index 3 was inherited AND re-executed by this attempt, where it
        failed.  The live record must win: banking it from the inherited
        copy would hand back a stale pass for work that has since been
        shown to fail, and the resume would report success without ever
        redoing it.
        """
        tc, pid, src, cap, pdir = client
        runs = TaskRunStorage(pdir)
        runs.append_iteration_summary(src, LOOP_ID, IterationSummary(
            index=3, status="failed", has_artifact=True, signature="deadbeef",
        ))
        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        assert 3 not in _banked(cap), (
            "index 3 was banked from the inherited record even though this "
            "attempt re-ran it and it failed -- the resume would skip it"
        )
        assert 3 in (set(range(WIDTH)) - _banked(cap))

    def test_an_inherited_index_with_no_artifact_file_is_not_banked(
            self, env, client):
        """Replaying an absent artifact drops outputs while counting it done."""
        tc, pid, src, cap, pdir = client
        missing = 7
        path = TaskRunStorage(pdir)._iteration_file(src, LOOP_ID, missing)
        path.unlink()
        assert _resume(tc, pid, src, LOOP_ID).status_code == 200
        assert missing not in _banked(cap), (
            f"index {missing} was banked with no artifact on disk; its "
            f"outputs would vanish from the loop result"
        )

    def test_inherited_indices_do_not_leak_into_a_different_loop(
            self, client):
        """``resume_iteration_artifacts`` is scoped to ONE loop.

        The run's inherited set belongs to LOOP_ID.  Resuming the sibling
        loop must not treat those indices as its own, or a second fan-out
        would report banked work it never performed.
        """
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, OTHER_LOOP_ID).status_code == 200
        assert _banked(cap) == set(), (
            f"resuming {OTHER_LOOP_ID} banked {sorted(_banked(cap))}, which "
            f"are {LOOP_ID}'s inherited indices"
        )


# ── Seam B: storage may seed an unseeded block, but only on request ─

class TestSeedingAnUnseededBlock:
    """Both halves of a contract that pulls in two directions.

    A callee loop legitimately has no state at launch, so refusing
    outright loses the prefix.  But storage must not mint a
    ``block_states`` entry for an id no card contains -- that was the
    pre-existing invariant, and it is what keeps a typo from
    materialising as a phantom row in the run map.
    """

    @pytest.fixture
    def storage(self, tmp_path):
        return TaskRunStorage(tmp_path)

    def _run(self, storage):
        return storage.create(TaskRunCreate(card_id="card-1"))

    def _replayed(self, i: int) -> IterationSummary:
        return IterationSummary(
            index=i, status="passed", has_artifact=True, replayed=True)

    def test_an_unknown_block_is_still_not_invented_by_default(
            self, storage):
        """The pre-existing invariant, unchanged."""
        run = self._run(storage)
        storage.seed_replayed_iterations(run.id, "nope", [self._replayed(0)])
        assert "nope" not in storage.get(run.id).block_states, (
            "storage minted state for an arbitrary block id; a typo would "
            "become a phantom row in the run map"
        )

    def test_a_verified_callee_block_can_be_seeded_on_request(
            self, storage):
        """The new half: an explicit opt-in from a caller that checked.

        Only the launch path passes this, and only after the resume
        endpoint has resolved the block against the card snapshot and the
        call snapshots -- so existence is established before storage is
        asked to create the record.
        """
        run = self._run(storage)
        storage.seed_replayed_iterations(
            run.id, LOOP_ID, [self._replayed(i) for i in (0, 1, 2)],
            create_if_missing=True,
        )
        state = storage.get(run.id).block_states.get(LOOP_ID)
        assert state is not None, (
            "the callee loop's prefix was dropped even with the opt-in; "
            "this is the write whose absence costs 52 re-runs later"
        )
        assert [s.index for s in state.iteration_summaries] == [0, 1, 2]
        assert all(s.replayed for s in state.iteration_summaries)

    def test_the_created_state_carries_the_declared_block_type(
            self, storage):
        """A state typed 'task' would break the run map's loop rendering."""
        run = self._run(storage)
        storage.seed_replayed_iterations(
            run.id, LOOP_ID, [self._replayed(0)],
            block_type="repeat", create_if_missing=True,
        )
        assert storage.get(run.id).block_states[LOOP_ID].block_type == "repeat"

    def test_the_merged_prefix_is_sorted(self, storage):
        """The fingerprint that proves the seeder ran.

        Insertion order is what the broken run showed (36, 50, 56, ...);
        a sorted strip is the seeder's signature.
        """
        run = self._run(storage)
        storage.seed_replayed_iterations(
            run.id, LOOP_ID,
            [self._replayed(i) for i in (9, 2, 5)],
            create_if_missing=True,
        )
        got = [s.index for s in
               storage.get(run.id).block_states[LOOP_ID].iteration_summaries]
        assert got == [2, 5, 9]

    def test_an_empty_prefix_creates_nothing(self, storage):
        """No records to install is not a reason to mint a state object."""
        run = self._run(storage)
        storage.seed_replayed_iterations(
            run.id, LOOP_ID, [], create_if_missing=True)
        assert LOOP_ID not in storage.get(run.id).block_states


# ── Seam C: the Call must not wipe the prefix ──────────────────────

class TestCalleeSeedingPreservesTheseededPrefix:
    """``set_block_state`` REPLACES; the prefix must survive anyway.

    Ordering makes this reachable: the prefix is installed at launch, and
    the Call seeds the callee subtree later, when it executes.  A blind
    seed therefore destroys a record that is already correct.
    """

    @pytest.fixture
    def storage(self, tmp_path):
        return TaskRunStorage(tmp_path)

    def test_existing_iteration_summaries_survive(self, storage):
        import app.agents.block_executor as be

        run = storage.create(TaskRunCreate(card_id="card-1"))
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id=LOOP_ID, block_type="repeat", status="queued",
            iteration_summaries=[
                IterationSummary(index=i, status="passed",
                                 has_artifact=True, replayed=True)
                for i in range(5)
            ],
        ))
        ctx = be.ExecutionContext(
            run_id=run.id, project_id="p", project_root="/tmp")
        ctx.storage = storage

        be._seed_callee_block_states(ctx, _callee_root())

        state = storage.get(run.id).block_states[LOOP_ID]
        assert [s.index for s in state.iteration_summaries] == [0, 1, 2, 3, 4], (
            "the Call's callee seeding wiped the replayed prefix; the next "
            "resume would re-run all five"
        )
        assert all(s.replayed for s in state.iteration_summaries)

    def test_the_rest_of_the_callee_subtree_is_still_seeded(self, storage):
        """The positive control: preservation must not disable seeding.

        Without this, a change that simply skipped every existing block
        would pass the test above while leaving the callee's other blocks
        unregistered -- which is the defect callee seeding exists to fix.
        """
        import app.agents.block_executor as be

        run = storage.create(TaskRunCreate(card_id="card-1"))
        ctx = be.ExecutionContext(
            run_id=run.id, project_id="p", project_root="/tmp")
        ctx.storage = storage

        be._seed_callee_block_states(ctx, _callee_root())

        seeded = storage.get(run.id).block_states
        for bid in ("b-cl1-root", "b-recon", LOOP_ID, "b-auditor",
                    OTHER_LOOP_ID, "b-merge"):
            assert bid in seeded, f"callee block {bid} was not seeded"

    def test_a_fresh_callee_loop_starts_empty(self, storage):
        """Preservation must not fabricate records where none existed."""
        import app.agents.block_executor as be

        run = storage.create(TaskRunCreate(card_id="card-1"))
        ctx = be.ExecutionContext(
            run_id=run.id, project_id="p", project_root="/tmp")
        ctx.storage = storage

        be._seed_callee_block_states(ctx, _callee_root())

        assert storage.get(run.id).block_states[LOOP_ID].iteration_summaries \
            == []


# ── The chain: A + B + C together ──────────────────────────────────

class TestTheChainHolds:
    """The test that would have caught the original defect.

    Seams A, B and C each passed in isolation while a chain of resumes
    leaked 50 banked iterations, because nothing exercised launch ->
    callee-seeding -> a SECOND resume.  This walks that path with the
    real storage layer and asserts on the outermost observable: what the
    second resume decides to bank.
    """

    @pytest.fixture
    def storage(self, tmp_path):
        return TaskRunStorage(tmp_path)

    def test_a_second_resume_still_banks_the_first_resumes_work(
            self, storage):
        import app.agents.block_executor as be
        from app.utils.resume_targets import parallel_replay_indices

        caller, callee = _caller_root(), _callee_root()
        banked_by_first = sorted(INHERITED)

        # 1. The first resume's launch: artifacts copied, prefix seeded.
        run = storage.create(TaskRunCreate(
            card_id="card-1", attempt=2, resume_kind="retry_from",
            resumed_from_block_id=LOOP_ID,
            resume_iteration_artifacts={
                i: Artifact(summary=f"inherited {i}", created_at=time.time())
                for i in banked_by_first
            },
        ))
        storage.set_card_snapshot(run.id, {
            "name": "CL0", "description": "", "root": caller.model_dump(),
        })
        for i in banked_by_first:
            storage.write_iteration_artifact(run.id, LOOP_ID, i, Artifact(
                summary=f"inherited {i}", created_at=time.time()))
        storage.seed_replayed_iterations(
            run.id, LOOP_ID,
            [IterationSummary(index=i, status="passed", has_artifact=True,
                              replayed=True) for i in banked_by_first],
            create_if_missing=True,
        )

        # 2. The Call executes and seeds the callee subtree.
        storage.record_call(run.id, CALL_ID, {
            "call_block_id": CALL_ID, "target": "CL1", "kind": "card",
            "key": "card:cl1", "root": callee.model_dump(),
        })
        ctx = be.ExecutionContext(
            run_id=run.id, project_id="p", project_root="/tmp")
        ctx.storage = storage
        be._seed_callee_block_states(ctx, callee)

        # 3. This attempt runs the remaining iterations; two fail again.
        for i in sorted(EXECUTED_PASSED):
            storage.write_iteration_artifact(run.id, LOOP_ID, i, Artifact(
                summary=f"audited {i}", created_at=time.time()))
            storage.append_iteration_summary(run.id, LOOP_ID, IterationSummary(
                index=i, status="passed", has_artifact=True))
        for i in sorted(EXECUTED_FAILED):
            storage.write_iteration_artifact(run.id, LOOP_ID, i, Artifact(
                summary=f"truncated {i}", created_at=time.time(), failed=True))
            storage.append_iteration_summary(run.id, LOOP_ID, IterationSummary(
                index=i, status="failed", has_artifact=True,
                signature="0f42e965acb2"))

        # 4. The SECOND resume's selection, from the record as it now stands.
        state = storage.get(run.id).block_states[LOOP_ID]
        selected = parallel_replay_indices(
            caller.model_dump(), LOOP_ID,
            [s.model_dump() for s in state.iteration_summaries],
            storage.get(run.id).call_snapshots,
        )

        assert set(selected or []) == INHERITED | EXECUTED_PASSED, (
            f"the second resume banked {len(selected or [])} of {WIDTH}; "
            f"the first resume's {len(banked_by_first)} banked iterations "
            f"leaked and would be re-run"
        )
        assert set(range(WIDTH)) - set(selected or []) == EXECUTED_FAILED

    def test_the_prefix_is_what_makes_the_second_resume_work(self, storage):
        """The failing counterpart: skip seeding and the leak returns.

        Pinning the mechanism, not just the outcome.  Without this a
        future change could satisfy the test above by some other route
        and leave the seeding path dead.
        """
        from app.utils.resume_targets import parallel_replay_indices

        caller, callee = _caller_root(), _callee_root()
        run = storage.create(TaskRunCreate(
            card_id="card-1", attempt=2, resumed_from_block_id=LOOP_ID))
        storage.set_card_snapshot(run.id, {
            "name": "CL0", "description": "", "root": caller.model_dump(),
        })
        storage.record_call(run.id, CALL_ID, {
            "call_block_id": CALL_ID, "target": "CL1", "kind": "card",
            "key": "card:cl1", "root": callee.model_dump(),
        })
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id=LOOP_ID, block_type="repeat", status="failed"))
        # No prefix seeded -- only this attempt's own six iterations.
        for i in sorted(EXECUTED_PASSED):
            storage.append_iteration_summary(run.id, LOOP_ID, IterationSummary(
                index=i, status="passed", has_artifact=True))
        for i in sorted(EXECUTED_FAILED):
            storage.append_iteration_summary(run.id, LOOP_ID, IterationSummary(
                index=i, status="failed", has_artifact=True))

        state = storage.get(run.id).block_states[LOOP_ID]
        selected = parallel_replay_indices(
            caller.model_dump(), LOOP_ID,
            [s.model_dump() for s in state.iteration_summaries],
            storage.get(run.id).call_snapshots,
        )
        assert set(selected or []) == EXECUTED_PASSED, (
            "selection consulted something other than the summaries; the "
            "endpoint's inherited-banking fallback is what covers this "
            "case and it must not be reachable from here"
        )
