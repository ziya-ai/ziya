"""Tests for the task-run API: cancel, iterations query, single artifact fetch.

These are thin route tests — the underlying storage and executor logic
is covered by tests/test_task_run_storage.py and tests/test_block_executor.py.
Here we verify the HTTP surface: status codes, filter semantics,
hydration behavior.
"""

import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.models.task_card import Artifact, Block
from app.models.task_run import (
    IterationStatus,
    IterationSummary,
    TaskRunBlockState,
    TaskRunCreate,
)
from app.storage.task_runs import TaskRunStorage
from app.storage.projects import ProjectStorage
from app.models.project import ProjectCreate
from app.api.task_runs import router


@pytest.fixture
def tmp_ziya_home(tmp_path, monkeypatch):
    """Point Ziya at a temp directory for this test."""
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def project_id(tmp_ziya_home):
    """Create a project and return its id."""
    storage = ProjectStorage(tmp_ziya_home)
    project = storage.create(ProjectCreate(name="test-project", path=str(tmp_ziya_home)))
    return project.id


@pytest.fixture
def client(tmp_ziya_home):
    """FastAPI test client wrapping just the task-runs router."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def run_storage(tmp_ziya_home, project_id):
    from app.utils.paths import get_project_dir
    return TaskRunStorage(get_project_dir(project_id))


def _mk_iteration(
    run_storage, run_id, block_id, index, status: IterationStatus,
    signature=None, has_artifact=True, artifact_summary="ok",
):
    """Append an iteration summary and (optionally) write its artifact."""
    run_storage.append_iteration_summary(
        run_id, block_id,
        IterationSummary(
            index=index, status=status, signature=signature,
            duration_ms=10, tokens=5, has_artifact=has_artifact,
        ),
    )
    if has_artifact:
        run_storage.write_iteration_artifact(
            run_id, block_id, index,
            Artifact(summary=artifact_summary, failed=(status == "failed"),
                     signature=signature),
        )


@pytest.fixture
def populated_run(run_storage):
    """Create a run with a Repeat block and 5 iterations of mixed status."""
    run = run_storage.create(TaskRunCreate(card_id="c1"))
    run_storage.set_block_state(run.id, TaskRunBlockState(
        block_id="rpt", block_type="repeat", status="running",
    ))
    _mk_iteration(run_storage, run.id, "rpt", 0, "passed")
    _mk_iteration(run_storage, run.id, "rpt", 1, "passed")
    _mk_iteration(run_storage, run.id, "rpt", 2, "failed", signature="sig-A")
    _mk_iteration(run_storage, run.id, "rpt", 3, "failed", signature="sig-B")
    _mk_iteration(run_storage, run.id, "rpt", 4, "failed", signature="sig-A")
    return run


class TestCancel:
    def test_cancel_running_run(self, client, project_id, run_storage):
        run = run_storage.create(TaskRunCreate(card_id="c1"))
        run_storage.update_status(run.id, "running")
        resp = client.post(
            f"/api/v1/projects/{project_id}/task-runs/{run.id}/cancel"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["cancel_requested"] is True

    def test_cancel_idempotent_on_done(self, client, project_id, run_storage):
        run = run_storage.create(TaskRunCreate(card_id="c1"))
        run_storage.update_status(run.id, "done")
        resp = client.post(
            f"/api/v1/projects/{project_id}/task-runs/{run.id}/cancel"
        )
        assert resp.status_code == 200, resp.text
        # Terminal runs return unchanged; cancel_requested stays False
        data = resp.json()
        assert data["cancel_requested"] is False

    def test_cancel_missing_run_404(self, client, project_id):
        resp = client.post(
            f"/api/v1/projects/{project_id}/task-runs/does-not-exist/cancel"
        )
        assert resp.status_code == 404


class TestIterationsQuery:
    def test_list_all(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 5
        # Sorted by block_id then index
        assert [r["summary"]["index"] for r in data["items"]] == [0, 1, 2, 3, 4]

    def test_filter_by_status_failed(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations",
            params={"status": "failed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert all(r["summary"]["status"] == "failed" for r in data["items"])

    def test_filter_by_signature(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations",
            params={"signature": "sig-A"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert {r["summary"]["index"] for r in data["items"]} == {2, 4}

    def test_combined_filters(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations",
            params={"status": "failed", "signature": "sig-B"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["summary"]["index"] == 3

    def test_pagination(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations",
            params={"limit": 2, "offset": 1},
        )
        data = resp.json()
        assert data["total"] == 5  # total is unfiltered match count
        assert len(data["items"]) == 2
        assert [r["summary"]["index"] for r in data["items"]] == [1, 2]

    def test_default_no_artifact_hydration(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations"
        )
        data = resp.json()
        for row in data["items"]:
            assert "artifact" not in row

    def test_include_artifact_hydrates(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}/iterations",
            params={"include": "artifact", "status": "failed"},
        )
        data = resp.json()
        assert data["total"] == 3
        for row in data["items"]:
            assert "artifact" in row
            assert row["artifact"] is not None
            assert row["artifact"]["failed"] is True

    def test_filter_by_block_id(self, client, project_id, run_storage):
        run = run_storage.create(TaskRunCreate(card_id="c1"))
        # Two separate Repeat blocks in the same run
        run_storage.set_block_state(run.id, TaskRunBlockState(
            block_id="rpt-a", block_type="repeat", status="running",
        ))
        run_storage.set_block_state(run.id, TaskRunBlockState(
            block_id="rpt-b", block_type="repeat", status="running",
        ))
        _mk_iteration(run_storage, run.id, "rpt-a", 0, "passed")
        _mk_iteration(run_storage, run.id, "rpt-a", 1, "passed")
        _mk_iteration(run_storage, run.id, "rpt-b", 0, "failed", signature="x")

        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{run.id}/iterations",
            params={"block_id": "rpt-a"},
        )
        data = resp.json()
        assert data["total"] == 2
        assert all(r["block_id"] == "rpt-a" for r in data["items"])

    def test_missing_run_404(self, client, project_id):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/does-not-exist/iterations"
        )
        assert resp.status_code == 404


class TestSingleArtifactFetch:
    def test_fetch_existing(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}"
            f"/iterations/rpt/2"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed"] is True
        assert data["signature"] == "sig-A"

    def test_fetch_missing_iteration_404(self, client, project_id, populated_run):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{populated_run.id}"
            f"/iterations/rpt/99"
        )
        assert resp.status_code == 404

    def test_fetch_summary_only_iteration_404(
        self, client, project_id, run_storage,
    ):
        """An iteration retained as summary-only (has_artifact=False) has no
        file on disk, so the fetch should 404."""
        run = run_storage.create(TaskRunCreate(card_id="c1"))
        run_storage.set_block_state(run.id, TaskRunBlockState(
            block_id="rpt", block_type="repeat", status="running",
        ))
        _mk_iteration(
            run_storage, run.id, "rpt", 0, "passed",
            has_artifact=False,
        )
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/{run.id}"
            f"/iterations/rpt/0"
        )
        assert resp.status_code == 404

    def test_fetch_missing_run_404(self, client, project_id):
        resp = client.get(
            f"/api/v1/projects/{project_id}/task-runs/does-not-exist"
            f"/iterations/any/0"
        )
        assert resp.status_code == 404
