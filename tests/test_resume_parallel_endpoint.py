"""The SEAM: resume-from on a parallel, through the real route.

Asserts on what ``_launch_run_for_card`` receives, because that is where
the two halves meet: the endpoint decides the resume point and which
branches to bank, and the executor (tests/test_resume_target_fanout.py)
honours ``resume_banked_ids``.  Either half alone passing proves nothing.

Fixture is the audit S7 run as observed: an OLD record held at one lane
(``lane_d``), from before holds were raised out of parallels.  Lane A had
finished; the rest had not.
"""

import json
import time
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact, TaskCardCreate
from app.models.task_run import TaskRunBlockState, TaskRunCreate
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage
from tests.test_resume_target_fanout import S7_ROOT, _blk


@pytest.fixture
def env(tmp_path):
    home = tmp_path / ".ziya"
    pid = "proj-par"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Parallel Resume", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    card = TaskCardStorage(pdir).create(
        TaskCardCreate(name="S7", root=_blk(S7_ROOT)))
    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(card_id=card.id))
    runs.set_card_snapshot(src.id, {"name": "S7", "root": S7_ROOT})

    def _state(bid, status, summary=None, failed=False):
        runs.set_block_state(src.id, TaskRunBlockState(
            block_id=bid, block_type="task", status=status,
            artifact=(Artifact(summary=summary, failed=failed,
                               created_at=time.time()) if summary else None),
        ))

    _state("givens", "done", "G")
    _state("lane_a", "done", "A found it")
    _state("f0", "failed", "boom", failed=True)
    for bid in ("lane_b", "lane_c", "lane_d", "lane_e"):
        _state(bid, "cancelled")
    # Written directly, as an older record would be, bypassing the raise
    # mark_held now applies.
    run = runs.get(src.id)
    run.status = "held"
    run.held_reason = "server_restart"
    run.held_at_block_id = "lane_d"
    runs._write_json(runs._run_file(src.id), run.model_dump())
    return home, pid, pdir, card.id, src.id


@pytest.fixture
def client(env):
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
        yield TestClient(app), pid, src_id, captured


def _resume(tc, pid, run_id, block_id, mode="retry"):
    return tc.post(
        f"/api/v1/projects/{pid}/task-runs/{run_id}"
        f"/resume-from/{block_id}?mode={mode}"
    )


class TestResumeInsideParallel:
    def test_retry_of_a_lane_resumes_the_outer_parallel(self, client):
        tc, pid, src, cap = client
        res = _resume(tc, pid, src, "lane_d")
        assert res.status_code == 200, res.text
        assert cap["resume_from_block_id"] == "foundation"
        # Lane A finished: replayed, not re-run.  F0 failed: re-runs.
        assert set(cap["resume_banked_ids"]) == {"lane_a"}
        assert cap["resumed_from_block_id"] == "foundation"

    def test_retry_of_the_parallel_banks_finished_branches(self, client):
        tc, pid, src, cap = client
        assert _resume(tc, pid, src, "foundation").status_code == 200
        assert cap["resume_from_block_id"] == "foundation"
        assert set(cap["resume_banked_ids"]) == {"lane_a"}

    def test_rerun_of_the_parallel_banks_nothing(self, client):
        tc, pid, src, cap = client
        res = _resume(tc, pid, src, "foundation", mode="rerun")
        assert res.status_code == 200, res.text
        assert cap["resume_from_block_id"] == "foundation"
        assert list(cap["resume_banked_ids"]) == []

    def test_continue_past_a_lane_accepts_it_and_resumes_the_parallel(self, client):
        # "Continue past F0" inside a parallel must not skip its siblings:
        # it means "accept F0's outcome", i.e. bank it alongside Lane A.
        tc, pid, src, cap = client
        assert _resume(tc, pid, src, "f0", mode="continue").status_code == 200
        assert cap["resume_from_block_id"] == "foundation"
        assert set(cap["resume_banked_ids"]) == {"lane_a", "f0"}

    def test_retry_outside_any_parallel_is_unchanged(self, client):
        tc, pid, src, cap = client
        assert _resume(tc, pid, src, "verify").status_code == 200
        assert cap["resume_from_block_id"] == "verify"
        assert list(cap["resume_banked_ids"]) == []
