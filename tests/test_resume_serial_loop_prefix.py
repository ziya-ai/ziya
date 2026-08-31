"""A block-level retry of a SERIAL loop must not re-run its banked prefix.

The defect, as reported: a run held on an infrastructure fault 22
iterations into a serial Repeat, resumed from the tile's own recovery
banner, restarted the loop at iteration 0 and re-paid for all 22.

Where it broke was a single hop.  Every piece of mid-loop machinery
already existed and worked:

  * ``block_executor._execute_repeat`` honours
    ``ctx.resume_from_iteration`` and replays the prefix;
  * ``resume_run_from_iteration`` (POST /resume-iteration/{block}/{i})
    computes that index and seeds the prefix correctly.

But the recovery banner does NOT call that endpoint.  ``recoveryTarget()``
yields only a block id, so the banner calls POST /resume-from/{block}
— and that endpoint's iteration-preserving code was gated on
``parallel_replay_indices``, which returns None for a serial loop.  So
the serial shape passed no ``resume_from_iteration`` at all and
``resume_at`` computed to 0.

Two things make this a defect rather than a deliberate semantic:

  1. ``RunRecoveryBanner`` renders "22 passed loop iterations will be
     replayed from record, not re-run" (from
     ``progressCounts(run).passedIterations``) and then re-ran them.
  2. The same endpoint already did the right thing for the parallel
     shape.  Serial was the asymmetry, not the rule.

The seam under test is therefore endpoint -> launch: does
POST /resume-from/{serial-loop}?mode=retry hand
``_launch_run_for_card`` a ``resume_from_iteration``?  The launch ->
executor hop is covered by test_resume_mid_loop_execution.py; asserting
only that hop is what let this bug live, since it passed throughout.

``TestTheKwargIsRealParameter`` exists because the harness below captures
the launch with ``**kwargs``, which would happily swallow a misspelled
keyword that real code rejects.  It checks the captured names against the
real signature so the seam cannot pass on a name nothing consumes.
"""

import inspect
import json
import time
from typing import Any, Dict, List, Optional, Set
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

# The real launch signature, captured at import time.  Read inside a test
# it would be the ``client`` fixture's mock instead, whose ``**kwargs``
# accepts everything — which is the very laxness this guards against.
from app.api.task_cards import _launch_run_for_card as _real_launch
LAUNCH_PARAMS = frozenset(inspect.signature(_real_launch).parameters)

CHAT_ID = "chat-serial-prefix"
SERIAL_LOOP = "b-campaign"
PARALLEL_LOOP = "b-fanout"
PLAIN_TASK = "b-recon"
WIDTH = 25
# The reported shape: 0..21 passed, 22 is where the credential died.
HELD_AT = 22
PASSED: Set[int] = set(range(0, HELD_AT))


def _root() -> Block:
    """A serial campaign, a parallel fan-out, and a plain task.

    The parallel loop is present so a regression guard can prove the
    serial branch did not capture the fan-out's index-set semantics, and
    the plain task so the "not a loop" path is exercised rather than
    assumed inert.
    """
    return Block(block_type="group", id="root", name="Study", body=[
        Block(block_type="task", id=PLAIN_TASK, name="Recon",
              instructions="map subsystems"),
        Block(
            block_type="repeat", id=SERIAL_LOOP, name="Serial campaign",
            repeat_mode="count", repeat_count=WIDTH,
            repeat_parallel=False, repeat_propagate="last",
            body=[Block(block_type="task", id="b-step", name="Step",
                        instructions="build on {{previous}}")],
        ),
        Block(
            block_type="repeat", id=PARALLEL_LOOP, name="Parallel fan-out",
            repeat_mode="count", repeat_count=WIDTH,
            repeat_parallel=True, repeat_propagate="none",
            body=[Block(block_type="task", id="b-agent", name="Agent",
                        instructions="audit")],
        ),
        Block(block_type="task", id="b-merge", name="Merge",
              instructions="merge"),
    ])


def _summaries(
    passed: Set[int], failed: Set[int], no_artifact: Set[int] = frozenset(),
) -> List[IterationSummary]:
    out = [
        IterationSummary(
            index=i, status="passed", has_artifact=i not in no_artifact,
            duration_ms=90_000, tokens=1_500_000,
        )
        for i in sorted(passed)
    ]
    out += [
        IterationSummary(index=i, status="failed", has_artifact=True,
                         signature="ab12cd34ef56",
                         duration_ms=110_000, tokens=1_800_000)
        for i in sorted(failed)
    ]
    return out


@pytest.fixture
def env(tmp_path):
    """A run held at iteration 22 of the serial loop."""
    home = tmp_path / ".ziya"
    pid = "proj-serial-prefix"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Serial Prefix", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))

    root = _root()
    card = TaskCardStorage(pdir).create(
        TaskCardCreate(name="Study", root=root))

    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(
        card_id=card.id, source_conversation_id=CHAT_ID))
    runs.set_card_snapshot(src.id, {
        "name": "Study", "description": "", "root": root.model_dump(),
    })

    # Artifacts for the 22 completed iterations, plus one for the held
    # index (the executor records before the fault propagates).
    for i in range(HELD_AT + 1):
        runs.write_iteration_artifact(src.id, SERIAL_LOOP, i, Artifact(
            summary=f"step {i}", created_at=time.time(),
            failed=i == HELD_AT,
        ))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=SERIAL_LOOP, block_type="repeat", status="held",
        iteration_summaries=_summaries(PASSED, {HELD_AT}),
    ))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=PLAIN_TASK, block_type="task", status="done",
        artifact=Artifact(summary="recon done", created_at=time.time()),
    ))
    runs.mark_held(
        src.id, reason="authentication_error", block_id=SERIAL_LOOP)
    return home, pid, pdir, card.id, src.id


@pytest.fixture
def client(env):
    """The real route, with the LAUNCH captured instead of performed."""
    home, pid, pdir, card_id, src_id = env
    captured: Dict[str, Any] = {}

    async def _capture(**kwargs):
        captured.clear()
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
    return {int(k) for k in (captured.get("resume_iteration_artifacts") or {})}


def _start(captured) -> Optional[int]:
    return captured.get("resume_from_iteration")


def _prefix(*args, **kwargs):
    """``serial_replay_prefix``, imported lazily.

    Deliberately not a module-level import: before the fix lands the name
    does not exist, and a module-level import turns every test here into a
    single collection error — which hides whether the endpoint assertions
    fail on the DEFECT or merely on a missing symbol.
    """
    from app.utils.resume_targets import serial_replay_prefix
    return serial_replay_prefix(*args, **kwargs)


# ── the fixture is the shape that was reported ─────────────────────

class TestTheFixtureIsTheReportedShape:
    """Without these, every assertion below could pass vacuously."""

    def test_the_loop_is_serial(self):
        from app.utils.resume_targets import find_block
        node = find_block(_root().model_dump(), SERIAL_LOOP)
        assert node is not None
        assert not node.get("repeat_parallel"), (
            "fixture is wrong: the loop must be SERIAL, since the parallel "
            "shape already banked its iterations correctly"
        )

    def test_the_parallel_path_reports_not_applicable_for_it(self):
        """Why the serial shape fell through the old gate."""
        from app.utils.resume_targets import parallel_replay_indices
        assert parallel_replay_indices(
            _root().model_dump(), SERIAL_LOOP, [], None,
        ) is None, (
            "fixture is wrong: parallel_replay_indices must return None "
            "here — that None is precisely what made the old code skip "
            "iteration banking for a serial loop"
        )

    def test_twenty_two_iterations_are_recorded_as_passed(self, env):
        *_, pdir, _card, src = env
        state = TaskRunStorage(pdir).get(src).block_states[SERIAL_LOOP]
        passed = {s.index for s in state.iteration_summaries
                  if s.status == "passed"}
        assert passed == PASSED and len(passed) == HELD_AT

    def test_their_artifacts_are_on_disk(self, env):
        *_, pdir, _card, src = env
        runs = TaskRunStorage(pdir)
        for i in sorted(PASSED):
            assert runs.read_iteration_artifact(
                src, SERIAL_LOOP, i) is not None, (
                f"fixture is wrong: iteration {i}'s artifact must exist, "
                f"since it is the work the resume is meant to preserve"
            )


# ── the seam: endpoint -> launch ───────────────────────────────────

class TestSerialLoopResumesMidLoop:
    """The reported bug, stated as an assertion."""

    def test_the_request_succeeds(self, client):
        tc, pid, src, _cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200

    def test_the_loop_restarts_at_the_held_iteration(self, client):
        """Fails without the fix: resume_from_iteration is absent."""
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200
        assert _start(cap) == HELD_AT, (
            f"loop restarts at {_start(cap)!r}, not {HELD_AT}. A None or 0 "
            f"here means block_executor computes resume_at=0 and re-runs "
            f"all {HELD_AT} banked iterations — the reported defect, and "
            f"the opposite of what the recovery banner promises."
        )

    def test_the_banked_prefix_is_carried(self, client):
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200
        assert _banked(cap) == PASSED, (
            f"banked {sorted(_banked(cap))}; expected 0..{HELD_AT - 1}. "
            f"Without the artifacts the replayed prefix cannot bind "
            f"{{{{previous}}}} for iteration {HELD_AT}."
        )

    def test_the_held_iteration_itself_re_runs(self, client):
        """The positive half: the resume is not a no-op.

        Paired with the assertion above deliberately.  Banking
        EVERYTHING would satisfy "nothing is re-run needlessly" while
        making the resume report success without redoing the work that
        failed — the worse of the two bugs.
        """
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200
        assert HELD_AT not in _banked(cap), (
            f"iteration {HELD_AT} was banked, so the resume would replay "
            f"the very iteration that faulted and report the loop complete"
        )
        assert _start(cap) == HELD_AT

    def test_the_prefix_is_carried_as_replayed_display_record(self, client):
        """Preserved iterations must be visible and excluded from progress."""
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200
        sums = cap.get("resume_iteration_summaries") or []
        by_index = {s.index: s for s in sums}
        assert set(by_index) == PASSED, (
            f"display record covers {sorted(by_index)}; a missing dot reads "
            f"as though the banked work was thrown away"
        )
        assert all(s.replayed for s in sums), (
            "replayed=True is what keeps a carried iteration out of every "
            "progress aggregate; without it the new run claims 22 passes "
            "it never executed"
        )
        assert all(s.status == "passed" for s in sums)

    def test_blocks_before_the_loop_still_replay(self, client):
        """The block-level guarantee must survive the mid-loop addition."""
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200
        assert PLAIN_TASK in (cap.get("resume_artifacts") or {})
        assert cap.get("resume_from_block_id") == SERIAL_LOOP


# ── the kwarg has to be one real code accepts ──────────────────────

class TestTheKwargIsRealParameter:
    """The capture harness uses **kwargs and would swallow a typo."""

    def test_launch_accepts_resume_from_iteration(self):
        assert "resume_from_iteration" in LAUNCH_PARAMS

    def test_every_captured_kwarg_is_accepted_by_the_real_launch(
        self, client,
    ):
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP).status_code == 200
        unknown = set(cap) - LAUNCH_PARAMS
        assert not unknown, (
            f"endpoint passes {sorted(unknown)}, which the real "
            f"_launch_run_for_card would reject — the mock's **kwargs hid "
            f"a TypeError that production would raise"
        )


# ── regressions the fix must not cause ─────────────────────────────

class TestUnaffectedPaths:

    def test_a_parallel_loop_still_has_no_start_index(self, env, client):
        """Index-set semantics must not acquire a prefix start.

        A parallel fan-out gives every iteration ``previous=None``, so an
        index carries no ordering; setting a start there would make the
        loop run FEWER iterations than the card asks for while reporting
        it complete.
        """
        _home, _pid, pdir, _card, src = env
        runs = TaskRunStorage(pdir)
        for i in range(5):
            runs.write_iteration_artifact(src, PARALLEL_LOOP, i, Artifact(
                summary=f"agent {i}", created_at=time.time()))
        runs.set_block_state(src, TaskRunBlockState(
            block_id=PARALLEL_LOOP, block_type="repeat", status="held",
            iteration_summaries=_summaries({0, 1, 2, 3}, {4}),
        ))
        tc, pid, src_id, cap, _p = client
        assert _resume(tc, pid, src_id, PARALLEL_LOOP).status_code == 200
        assert _start(cap) is None, (
            f"parallel loop got resume_from_iteration={_start(cap)!r}; a "
            f"fan-out resumes by index SET, not by prefix"
        )
        assert _banked(cap) == {0, 1, 2, 3}

    def test_continue_mode_banks_no_iterations(self, client):
        """A continue resumes AFTER the loop, which then replays whole."""
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, SERIAL_LOOP, "continue").status_code == 200
        assert _start(cap) is None and _banked(cap) == set(), (
            "a continue past the loop must not re-plan its iterations"
        )

    def test_a_plain_task_target_gets_no_start_index(self, client):
        tc, pid, src, cap, _pdir = client
        assert _resume(tc, pid, src, PLAIN_TASK).status_code == 200
        assert _start(cap) is None and _banked(cap) == set()


# ── the prefix rule itself ─────────────────────────────────────────

class TestSerialReplayPrefix:
    """Unit coverage for the walk, including the refusals."""

    ROOT = staticmethod(lambda: _root().model_dump())

    def test_returns_none_for_a_parallel_loop(self):
        assert _prefix(
            self.ROOT(), PARALLEL_LOOP,
            [s.model_dump() for s in _summaries({0, 1}, set())],
        ) is None

    def test_returns_none_for_a_non_loop(self):
        assert _prefix(self.ROOT(), PLAIN_TASK, []) is None

    def test_returns_none_for_an_unknown_block(self):
        assert _prefix(self.ROOT(), "b-nope", []) is None

    def test_zero_when_nothing_is_recorded(self):
        assert _prefix(self.ROOT(), SERIAL_LOOP, []) == 0

    def test_stops_at_the_first_failure(self):
        sums = [s.model_dump() for s in _summaries(set(range(0, 7)), {7, 8})]
        assert _prefix(self.ROOT(), SERIAL_LOOP, sums) == 7

    def test_zero_when_the_first_iteration_failed(self):
        sums = [s.model_dump() for s in _summaries(set(), {0})]
        assert _prefix(self.ROOT(), SERIAL_LOOP, sums) == 0

    def test_stops_at_a_pass_whose_artifact_was_not_retained(self):
        """The 50-pass retention cap, degrading honestly.

        Iteration 10 passed but holds only a summary, so it cannot supply
        {{previous}} to 11.  Banking 0..9 and re-running from 10 is
        strictly better than the whole-loop re-run that was previously
        the only option — and unlike skipping the hole, it cannot feed an
        empty {{previous}} to the first executed iteration.
        """
        sums = [s.model_dump() for s in _summaries(
            set(range(0, 20)), set(), no_artifact={10})]
        assert _prefix(self.ROOT(), SERIAL_LOOP, sums) == 10

    def test_stops_at_a_gap_in_the_record(self):
        """A hole is fatal for a dependent loop, so the prefix ends there."""
        sums = [s.model_dump() for s in _summaries({0, 1, 2, 4, 5}, set())]
        assert _prefix(self.ROOT(), SERIAL_LOOP, sums) == 3

    def test_inherited_indices_extend_the_prefix(self):
        """A chain of resumes must not shed its prefix one attempt at a time.

        A run that is itself a resume records summaries only for what it
        EXECUTED, so indices 0..3 here exist solely as carried artifacts.
        Ignoring them would restart the loop at 0 on the second resume.
        """
        sums = [s.model_dump() for s in _summaries({4, 5, 6}, {7})]
        assert _prefix(
            self.ROOT(), SERIAL_LOOP, sums, inherited={0, 1, 2, 3},
        ) == 7
        assert _prefix(
            self.ROOT(), SERIAL_LOOP, sums,
        ) == 0, "without the carried set the prefix cannot start"
