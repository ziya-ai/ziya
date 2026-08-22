"""
POST /task-runs/{run_id}/resume-iteration/{block_id}/{index} — the
replayed-prefix side effect, end to end.

Why this file exists separately from test_resume_replayed_prefix.py: that
one covers the storage merge and the progress exclusions in isolation,
but nothing exercised the ENDPOINT, which is where the prefix is actually
built.  The bridge between "user clicks dot N" and "greyed dots appear"
is the ``for idx in range(start)`` loop in ``resume_run_from_iteration``,
and its two branches (a recorded summary vs. an artifact inherited from
an attempt further back) had no coverage at all — so a resume could pass
every unit test while the new run's dot strip still restarted at one.

These drive the real endpoint against real storage with only
``execute_block`` stubbed, and assert on what lands in the NEW run's
record — which is exactly what the run map reads.
"""

import json
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import IterationSummary, TaskRunBlockState, TaskRunCreate
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage

CHAT_ID = "chat-iter-resume"
LOOP_ID = "loop"


def _loop_root():
    """Group(prep, repeat x5) — the shape a mid-loop resume targets."""
    return Block(block_type="group", id="g", body=[
        Block(block_type="task", id="prep", name="Prep", instructions="prep"),
        Block(
            block_type="repeat", id=LOOP_ID, name="Campaign",
            repeat_mode="count", repeat_count=5, repeat_propagate="last",
            body=[Block(block_type="task", id="inner", instructions="work")],
        ),
    ])


@pytest.fixture
def env(tmp_path):
    home = tmp_path / ".ziya"
    pid = "proj-iter-resume"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Iter Resume", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))

    root = _loop_root()
    card = TaskCardStorage(pdir).create(TaskCardCreate(name="Card", root=root))

    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(
        card_id=card.id, source_conversation_id=CHAT_ID))
    runs.set_card_snapshot(src.id, {
        "name": "Card", "description": "", "root": root.model_dump(),
    })
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=LOOP_ID, block_type="repeat", status="failed",
    ))
    return home, pid, pdir, card.id, src.id, runs


@pytest.fixture
def client(env):
    home, pid, pdir, card_id, src_id, runs = env

    async def _stub_execute(block, ctx):
        # The executor is not under test; the prefix is seeded at launch,
        # before this would run.
        return Artifact(summary="stub", created_at=time.time())

    with patch("app.api.task_runs.get_ziya_home", return_value=home), \
         patch("app.api.task_runs.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.get_ziya_home", return_value=home), \
         patch("app.api.task_cards.get_project_dir", return_value=pdir), \
         patch("app.api.task_cards.execute_block", new=_stub_execute):
        from app.api.task_runs import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), pid, pdir, card_id, src_id, runs


def _record(runs, run_id, index, status="passed", with_artifact=True):
    """Record one iteration on the source run, as the executor would."""
    runs.append_iteration_summary(run_id, LOOP_ID, IterationSummary(
        index=index, status=status,
        signature=("sig-x" if status == "failed" else None),
        duration_ms=100 + index, tokens=index,
        has_artifact=with_artifact,
    ))
    if with_artifact:
        runs.write_iteration_artifact(
            run_id, LOOP_ID, index,
            Artifact(summary=f"iteration {index} output", created_at=0.0),
        )


def _resume(tc, pid, run_id, index, mode="retry_iteration"):
    return tc.post(
        f"/api/v1/projects/{pid}/task-runs/{run_id}"
        f"/resume-iteration/{LOOP_ID}/{index}",
        params={"mode": mode},
    )


def _prefix_of(runs, run_id):
    """The new run's loop summaries, index-ordered."""
    fresh = runs.get(run_id)
    return fresh.block_states[LOOP_ID].iteration_summaries


class TestRetryPrefix:
    """Retry at 3 of a 5-iteration run: 0-2 preserved, 3 re-run."""

    def test_prefix_is_seeded_on_the_new_run(self, client):
        tc, pid, pdir, _, src_id, runs = client
        for i in range(5):
            _record(runs, src_id, i)

        res = _resume(tc, pid, src_id, 3)
        assert res.status_code == 200, res.text
        new_id = res.json()["run"]["id"]

        got = _prefix_of(runs, new_id)
        # The headline assertion: three dots exist where the bug left
        # zero, so the strip counts from the true loop position.
        assert [s.index for s in got] == [0, 1, 2]
        assert all(s.replayed for s in got)

    def test_preserved_failure_keeps_its_status_and_signature(self, client):
        # Recolouring a preserved failure green would misreport the
        # source attempt's outcome on the resumed run's face.
        tc, pid, pdir, _, src_id, runs = client
        _record(runs, src_id, 0, status="passed")
        _record(runs, src_id, 1, status="failed")
        _record(runs, src_id, 2, status="passed")
        _record(runs, src_id, 3, status="failed")

        new_id = _resume(tc, pid, src_id, 3).json()["run"]["id"]
        got = {s.index: s for s in _prefix_of(runs, new_id)}
        assert got[1].status == "failed"
        assert got[1].signature == "sig-x"
        assert got[0].status == "passed"

    def test_timings_are_carried_verbatim(self, client):
        tc, pid, pdir, _, src_id, runs = client
        for i in range(4):
            _record(runs, src_id, i)
        new_id = _resume(tc, pid, src_id, 3).json()["run"]["id"]
        got = {s.index: s for s in _prefix_of(runs, new_id)}
        assert (got[2].duration_ms, got[2].tokens) == (102, 2)

    def test_carried_artifacts_are_readable_under_the_new_run(self, client):
        # The dot's open action fetches
        # /task-runs/{new_run}/iterations/{block}/{index}; without the
        # copy every replayed dot is a visible circle that 404s.
        tc, pid, pdir, _, src_id, runs = client
        for i in range(4):
            _record(runs, src_id, i)
        new_id = _resume(tc, pid, src_id, 3).json()["run"]["id"]

        for i in range(3):
            got = runs.read_iteration_artifact(new_id, LOOP_ID, i)
            assert got is not None, f"iteration {i} artifact not copied"
            assert got.summary == f"iteration {i} output"

    def test_the_open_endpoint_serves_a_replayed_iteration(self, client):
        # The same thing through HTTP, which is what the UI does.
        tc, pid, pdir, _, src_id, runs = client
        for i in range(4):
            _record(runs, src_id, i)
        new_id = _resume(tc, pid, src_id, 3).json()["run"]["id"]

        res = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{new_id}"
            f"/iterations/{LOOP_ID}/1")
        assert res.status_code == 200, res.text
        assert res.json()["summary"] == "iteration 1 output"

    def test_resume_at_zero_seeds_nothing(self, client):
        # Nothing was preserved, so no dots should be invented.
        tc, pid, pdir, _, src_id, runs = client
        for i in range(3):
            _record(runs, src_id, i)
        new_id = _resume(tc, pid, src_id, 0).json()["run"]["id"]
        assert _prefix_of(runs, new_id) == []

    def test_source_run_record_is_untouched(self, client):
        tc, pid, pdir, _, src_id, runs = client
        for i in range(5):
            _record(runs, src_id, i)
        _resume(tc, pid, src_id, 3)
        got = _prefix_of(runs, src_id)
        assert [s.index for s in got] == [0, 1, 2, 3, 4]
        assert not any(s.replayed for s in got)


class TestContinuePrefix:
    """Continue from 2 accepts 2's result, so the prefix covers 0-2."""

    def test_continue_includes_the_accepted_iteration(self, client):
        tc, pid, pdir, _, src_id, runs = client
        for i in range(5):
            _record(runs, src_id, i)
        new_id = _resume(
            tc, pid, src_id, 2, mode="continue_iteration",
        ).json()["run"]["id"]
        got = _prefix_of(runs, new_id)
        assert [s.index for s in got] == [0, 1, 2]
        assert all(s.replayed for s in got)


class TestRetentionAndChaining:
    def test_unretained_iteration_still_gets_a_dot(self, client):
        # An iteration past the pass-retention cap has no artifact, but it
        # DID happen.  Omitting the dot would answer "did this run?"
        # wrongly; has_artifact=False marks it unopenable instead.
        tc, pid, pdir, _, src_id, runs = client
        _record(runs, src_id, 0, with_artifact=False)
        _record(runs, src_id, 1)
        _record(runs, src_id, 2)

        new_id = _resume(tc, pid, src_id, 2).json()["run"]["id"]
        got = {s.index: s for s in _prefix_of(runs, new_id)}
        assert set(got) == {0, 1}
        assert got[0].has_artifact is False
        assert got[1].has_artifact is True

    def test_a_chained_resume_keeps_the_whole_prefix(self, client):
        # The regression that matters for long campaigns: resuming a
        # resume must not shed the earliest iterations one attempt at a
        # time.  Attempt 2 resumes at 2, attempt 3 at 3 — attempt 3 must
        # still show dots 0,1,2.
        tc, pid, pdir, _, src_id, runs = client
        for i in range(4):
            _record(runs, src_id, i)

        second = _resume(tc, pid, src_id, 2).json()["run"]["id"]
        # Attempt 2 executes 2 and 3, as the executor would record them.
        _record(runs, second, 2)
        _record(runs, second, 3)
        runs.update_status(second, "failed")

        third = _resume(tc, pid, second, 3).json()["run"]["id"]
        got = _prefix_of(runs, third)
        assert [s.index for s in got] == [0, 1, 2]
        assert all(s.replayed for s in got)

    def test_chained_resume_carries_inherited_artifacts_forward(self, client):
        tc, pid, pdir, _, src_id, runs = client
        for i in range(4):
            _record(runs, src_id, i)
        second = _resume(tc, pid, src_id, 2).json()["run"]["id"]
        _record(runs, second, 2)
        _record(runs, second, 3)
        runs.update_status(second, "failed")

        third = _resume(tc, pid, second, 3).json()["run"]["id"]
        # Index 0 was inherited by attempt 2 and must reach attempt 3.
        got = runs.read_iteration_artifact(third, LOOP_ID, 0)
        assert got is not None and got.summary == "iteration 0 output"
