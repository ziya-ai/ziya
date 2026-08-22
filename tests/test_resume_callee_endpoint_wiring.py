"""The SEAM: the resume-from endpoint, for a hold inside a called card.

Two halves of this fix were already covered in isolation —
``test_resume_through_call.py`` (resolution) and
``test_resume_call_descent_execution.py`` (the executor walk) — and both
passed while the feature was broken end to end, because nothing asserted
the endpoint that joins them.  A ``resume_call_chain()`` call sat in
``resume_run_from_iteration`` with its import bound only in a SIBLING
function, so every request to that endpoint raised ``NameError`` at line
862.  Two correct halves, never connected.

So this drives the real FastAPI route against a real run record and
asserts on what ``_launch_run_for_card`` actually receives, which is the
outermost observable surface before execution begins.  Four things have
to be true simultaneously, and each was independently wrong at some point
during this work:

  1. ``resume_from_block_id`` is the CALLEE block, passed through
     untouched.  An earlier attempt substituted the enclosing Call block
     here, which resolved the 404 while re-entering the callee from its
     own start — a 14-hour re-run wearing the label "resume".
  2. ``resume_call_chain`` names the Call to descend through.  Without it
     the executor's gate replays the Call whole (empty ``body`` ->
     ``_subtree_contains`` False) and the run reports success having
     executed nothing, which is worse than the 404 it replaced.
  3. ``resume_iteration_artifacts`` holds the banked passes, so the
     fan-out re-runs only what never finished.
  4. The failed iteration is NOT banked — banking it would make the
     resume a no-op that reports success.

The fixture is the reported study's shape deliberately: a 20-wide auditor
fan-out inside Phase 1 of a six-phase orchestrator, held on
``authentication_error`` with 19 passes banked.
"""

import json
import time
from typing import Any, Dict, List
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

CHAT_ID = "chat-callee-resume"
WIDTH = 20
FAILED_INDEX = 19
LOOP_ID = "b-cf96c4e2"
CALL_ID = "call-p1"


def _callee_root() -> Block:
    """CL1: recon, the auditor fan-out, then a merge."""
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
    """A project holding a run HELD inside a called card's fan-out."""
    home = tmp_path / ".ziya"
    pid = "proj-callee"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Callee Resume", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))

    caller, callee = _caller_root(), _callee_root()
    card = TaskCardStorage(pdir).create(
        TaskCardCreate(name="CL0", root=caller))

    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(
        card_id=card.id, source_conversation_id=CHAT_ID))
    # The caller's snapshot does NOT contain the callee's blocks: a Call is
    # named, not inlined.  That asymmetry is the whole defect.
    runs.set_card_snapshot(src.id, {
        "name": "CL0", "description": "", "root": caller.model_dump(),
    })
    # ...the callee's tree lives here, keyed by the CALL block's id.
    runs.record_call(src.id, CALL_ID, {
        "call_block_id": CALL_ID, "target": "CL1", "kind": "card",
        "key": "card:cl1", "root": callee.model_dump(),
    })

    # The fan-out's own record: 19 passes with retained artifacts, one
    # failure.  Callee blocks DO persist in block_states even though their
    # tree does not appear in card_snapshot.
    summaries: List[IterationSummary] = []
    for i in range(WIDTH):
        passed = i != FAILED_INDEX
        summaries.append(IterationSummary(
            index=i, status="passed" if passed else "failed",
            duration_ms=2_520_000, tokens=180_000, has_artifact=True,
        ))
        runs.write_iteration_artifact(src.id, LOOP_ID, i, Artifact(
            summary=f"subsystem {i} audited", created_at=time.time(),
            failed=not passed,
        ))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=LOOP_ID, block_type="repeat", status="failed",
        iteration_summaries=summaries,
    ))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id="b-recon", block_type="task", status="done",
        artifact=Artifact(summary="recon done", created_at=time.time()),
    ))
    runs.mark_held(
        src.id, reason="authentication_error", block_id=LOOP_ID,
        error="AWS credentials have expired.",
        faults={
            "fault_count": 1, "fanout_width": WIDTH,
            "primary_kind": "authentication_error",
            "kinds": {"authentication_error": 1},
            "call_path": ["CL0", "CL1"], "fleet_wide": True,
            "block_ids": [LOOP_ID],
        },
    )
    return home, pid, pdir, card.id, src.id


@pytest.fixture
def client(env):
    """The real route, with the LAUNCH captured instead of performed."""
    home, pid, pdir, card_id, src_id = env
    captured: Dict[str, Any] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        # A TaskRun is the declared return type and the endpoint reads
        # ``.id`` off it for the binding, so return a real one.
        return TaskRunStorage(pdir).create(TaskRunCreate(card_id=card_id))

    with patch("app.api.task_runs.get_ziya_home", return_value=home), \
         patch("app.api.task_runs.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards._launch_run_for_card", new=_capture):
        from app.api.task_runs import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), pid, src_id, captured


def _resume(tc, pid, run_id, block_id, mode="retry"):
    return tc.post(
        f"/api/v1/projects/{pid}/task-runs/{run_id}"
        f"/resume-from/{block_id}?mode={mode}"
    )


class TestHoldInsideACalledCardIsResumable:
    """The 404 this fix exists to remove."""

    def test_the_request_succeeds(self, client):
        tc, pid, src_id, _ = client
        res = _resume(tc, pid, src_id, LOOP_ID)
        assert res.status_code == 200, (
            f"resuming a hold inside a called card still fails: {res.text}"
        )

    def test_the_callee_block_is_not_in_the_card_snapshot(self, client):
        """Proves the fixture reproduces the real asymmetry.

        Without this the test above could pass against a run whose
        snapshot happened to contain the block, which is the one shape
        that never had a bug.
        """
        from app.utils.resume_targets import find_block
        snap = _caller_root().model_dump()
        assert find_block(snap, LOOP_ID) is None, (
            "fixture is wrong: the callee loop must NOT be in the caller's "
            "own tree, or this suite proves nothing"
        )


class TestTheResumePointIsTheCalleeBlockItself:
    """Not the Call block — that was the rejected design."""

    def test_resume_from_block_id_is_the_callee_loop(self, client):
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        assert cap.get("resume_from_block_id") == LOOP_ID, (
            f"resume point is {cap.get('resume_from_block_id')!r}; "
            f"substituting the Call block re-enters the callee from its "
            f"own start, which discards the banked iterations"
        )

    def test_the_call_is_named_for_descent(self, client):
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        assert cap.get("resume_call_chain") == [CALL_ID], (
            f"resume_call_chain is {cap.get('resume_call_chain')!r}; without "
            f"the Call id the gate replays the whole phase and the run "
            f"executes nothing"
        )

    def test_the_user_facing_target_is_reported(self, client):
        """``resumed_from_block_id`` is what the UI says it resumed from."""
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        assert cap.get("resumed_from_block_id") == LOOP_ID


class TestOnlyTheUnfinishedIterationRuns:
    """The 14 hours."""

    def test_nineteen_passes_are_banked(self, client):
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        banked = cap.get("resume_iteration_artifacts") or {}
        assert sorted(banked) == list(range(WIDTH - 1)), (
            f"banked {sorted(banked)}; expected 0..{WIDTH - 2} — every "
            f"index not banked is a subagent that re-runs"
        )

    def test_the_failed_iteration_is_not_banked(self, client):
        """Banking it would make the resume a no-op reporting success."""
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        assert FAILED_INDEX not in (cap.get("resume_iteration_artifacts") or {})

    def test_the_banked_artifacts_carry_real_content(self, client):
        """Read off disk, not fabricated — an empty replay drops outputs."""
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        banked = cap.get("resume_iteration_artifacts") or {}
        assert banked, "nothing banked at all"
        first = banked[sorted(banked)[0]]
        summary = getattr(first, "summary", None) or (
            first.get("summary") if isinstance(first, dict) else None)
        assert summary and "audited" in summary, (
            f"banked artifact has no recorded content: {first!r}"
        )

    def test_the_display_prefix_marks_them_replayed(self, client):
        """``replayed=True`` keeps them out of every progress aggregate."""
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        prefix = cap.get("resume_iteration_summaries") or []
        assert len(prefix) == WIDTH - 1, (
            f"{len(prefix)} display records for {WIDTH - 1} banked "
            f"iterations — a missing dot reads as discarded work"
        )
        assert all(getattr(s, "replayed", False) for s in prefix), (
            "a carried iteration not marked replayed inflates this "
            "attempt's own progress with a prior attempt's results"
        )


class TestPriorPhasesStillReplay:
    """Descent must not turn into re-execution of everything."""

    def test_completed_blocks_are_handed_over_for_replay(self, client):
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID)
        replay = cap.get("resume_artifacts") or {}
        assert "b-recon" in replay, (
            "b-recon completed and must replay from record rather than "
            "re-run; it is inside the same callee as the resume target"
        )


class TestContinueDoesNotBankIterations:
    """A continue resumes AFTER the loop, so its iterations never re-plan.

    Banking them anyway would be harmless today but silently wrong the
    moment the loop is re-entered for any other reason, and the asymmetry
    is easy to lose in a refactor.
    """

    def test_continue_banks_nothing(self, client):
        tc, pid, src_id, cap = client
        res = _resume(tc, pid, src_id, LOOP_ID, mode="continue")
        assert res.status_code == 200, res.text
        assert not (cap.get("resume_iteration_artifacts") or {})

    def test_continue_still_descends(self, client):
        """The successor is inside the callee too, so the chain is needed."""
        tc, pid, src_id, cap = client
        _resume(tc, pid, src_id, LOOP_ID, mode="continue")
        assert cap.get("resume_call_chain") == [CALL_ID]
        assert cap.get("resume_from_block_id") == "b-merge", (
            f"continue landed on {cap.get('resume_from_block_id')!r}; the "
            f"block after the fan-out is b-merge, inside the same callee"
        )
