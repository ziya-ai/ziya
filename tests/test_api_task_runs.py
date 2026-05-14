"""
Tests for app.api.task_runs — the endpoints added in Slice D.

Covers:
  - POST /cancel sets the soft-cancel flag
  - POST /cancel is idempotent on terminal runs
  - GET /iterations returns filtered summaries
  - GET /iterations supports status + signature filters
  - GET /iterations?include=artifact hydrates full Artifacts
  - GET /iterations/{block_id}/{index} returns a single Artifact
  - Missing/404 paths return 404
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact
from app.models.task_run import (
    IterationSummary, TaskRun, TaskRunBlockState, TaskRunCreate,
)
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-proj-runs"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    project_data = {
        "id": project_id,
        "name": "Runs Test",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }
    (proj_dir / "project.json").write_text(json.dumps(project_data))
    return project_id


@pytest.fixture
def client(ziya_home, project_dir):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.task_runs.get_ziya_home", return_value=ziya_home):
            with patch(
                "app.api.task_runs.get_project_dir",
                return_value=ziya_home / "projects" / project_dir,
            ):
                from app.api.task_runs import router
                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_dir, ziya_home


@pytest.fixture
def storage(client):
    _, pid, home = client
    return TaskRunStorage(home / "projects" / pid)


def _seed_run_with_iterations(storage):
    """Create a run with one Repeat block whose state contains a mix of
    passing and failing iteration summaries.  Returns (run_id, block_id)."""
    run = storage.create(TaskRunCreate(card_id="fake-card"))
    block_id = "repeat-1"
    storage.set_block_state(run.id, TaskRunBlockState(
        block_id=block_id, block_type="repeat", status="running",
    ))
    # 5 summaries: 3 pass, 2 fail (one shared signature across the fails)
    iters = [
        IterationSummary(index=0, status="passed", duration_ms=10, tokens=1),
        IterationSummary(index=1, status="failed", signature="sig-a", duration_ms=12, tokens=2),
        IterationSummary(index=2, status="passed", duration_ms=9, tokens=1),
        IterationSummary(index=3, status="failed", signature="sig-a", duration_ms=11, tokens=2),
        IterationSummary(index=4, status="passed", duration_ms=8, tokens=1),
    ]
    for s in iters:
        storage.append_iteration_summary(run.id, block_id, s)
    return run.id, block_id


class TestCancel:
    def test_cancel_sets_flag(self, client, storage):
        tc, pid, _ = client
        run = storage.create(TaskRunCreate(card_id="c"))
        storage.update_status(run.id, "running")

        resp = tc.post(
            f"/api/v1/projects/{pid}/task-runs/{run.id}/cancel",
        )
        assert resp.status_code == 200
        assert resp.json()["cancel_requested"] is True

        # And it persisted
        assert storage.get(run.id).cancel_requested is True

    def test_cancel_missing_run_404(self, client):
        tc, pid, _ = client
        resp = tc.post(f"/api/v1/projects/{pid}/task-runs/missing/cancel")
        assert resp.status_code == 404

    def test_cancel_is_idempotent_on_terminal(self, client, storage):
        tc, pid, _ = client
        run = storage.create(TaskRunCreate(card_id="c"))
        storage.update_status(run.id, "done")

        resp = tc.post(f"/api/v1/projects/{pid}/task-runs/{run.id}/cancel")
        assert resp.status_code == 200
        # Flag NOT set — terminal runs are no-ops
        assert resp.json()["cancel_requested"] is False


class TestIterationsQuery:
    def test_returns_all_iterations_without_filter(self, client, storage):
        tc, pid, _ = client
        run_id, _ = _seed_run_with_iterations(storage)

        resp = tc.get(f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 5
        # Ordered by (block_id, index)
        assert [it["summary"]["index"] for it in body["items"]] == [0, 1, 2, 3, 4]

    def test_status_filter(self, client, storage):
        tc, pid, _ = client
        run_id, _ = _seed_run_with_iterations(storage)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations",
            params={"status": "failed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert all(it["summary"]["status"] == "failed" for it in body["items"])

    def test_signature_filter(self, client, storage):
        tc, pid, _ = client
        run_id, _ = _seed_run_with_iterations(storage)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations",
            params={"signature": "sig-a"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2

    def test_block_id_filter(self, client, storage):
        tc, pid, _ = client
        run_id, block_id = _seed_run_with_iterations(storage)

        # Matching block_id
        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations",
            params={"block_id": block_id},
        )
        assert resp.json()["total"] == 5

        # Non-matching block_id
        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations",
            params={"block_id": "other-block"},
        )
        assert resp.json()["total"] == 0

    def test_limit_and_offset(self, client, storage):
        tc, pid, _ = client
        run_id, _ = _seed_run_with_iterations(storage)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations",
            params={"limit": 2, "offset": 1},
        )
        body = resp.json()
        assert body["total"] == 5  # total before pagination
        assert len(body["items"]) == 2
        assert [it["summary"]["index"] for it in body["items"]] == [1, 2]

    def test_include_artifact_hydrates(self, client, storage):
        tc, pid, _ = client
        run_id, block_id = _seed_run_with_iterations(storage)

        # Write a full artifact for iter 1 (which is retained=has_artifact=True)
        art = Artifact(summary="failed body", failed=True, signature="sig-a")
        storage.write_iteration_artifact(run_id, block_id, 1, art)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations",
            params={"status": "failed", "include": "artifact"},
        )
        body = resp.json()
        # Iter 1 should be hydrated
        item_1 = next(it for it in body["items"] if it["summary"]["index"] == 1)
        assert item_1["artifact"] is not None
        assert item_1["artifact"]["summary"] == "failed body"
        # Iter 3 has no file on disk → artifact is None (but still listed)
        item_3 = next(it for it in body["items"] if it["summary"]["index"] == 3)
        assert "artifact" in item_3
        assert item_3["artifact"] is None

    def test_default_does_not_hydrate(self, client, storage):
        tc, pid, _ = client
        run_id, _ = _seed_run_with_iterations(storage)

        resp = tc.get(f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations")
        body = resp.json()
        # No 'artifact' key present when include is empty
        for item in body["items"]:
            assert "artifact" not in item

    def test_missing_run_404(self, client):
        tc, pid, _ = client
        resp = tc.get(f"/api/v1/projects/{pid}/task-runs/missing/iterations")
        assert resp.status_code == 404


class TestIterationArtifactFetch:
    def test_fetch_existing_artifact(self, client, storage):
        tc, pid, _ = client
        run_id, block_id = _seed_run_with_iterations(storage)

        art = Artifact(summary="one", failed=False)
        storage.write_iteration_artifact(run_id, block_id, 0, art)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}"
            f"/iterations/{block_id}/0",
        )
        assert resp.status_code == 200
        assert resp.json()["summary"] == "one"

    def test_fetch_missing_artifact_404(self, client, storage):
        tc, pid, _ = client
        run_id, block_id = _seed_run_with_iterations(storage)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}"
            f"/iterations/{block_id}/999",
        )
        assert resp.status_code == 404

    def test_fetch_on_missing_run_404(self, client):
        tc, pid, _ = client
        resp = tc.get(
            f"/api/v1/projects/{pid}/task-runs/nope/iterations/x/0",
        )
        assert resp.status_code == 404
