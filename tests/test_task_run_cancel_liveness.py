"""Cancel endpoint: live-executor detection must cross instance boundaries.

``_get_storage`` builds a fresh TaskRunStorage per request, so the
process-local ``_active_runs`` set the executor populates is never the one
the cancel endpoint consults.  Before the fix the endpoint therefore took
the force-cancel path for EVERY run and overwrote status to ``cancelled``
under a live executor.  The executor lock (``executor_alive``) is
per-directory, not per-instance, so it is the correct liveness probe here.
"""

import pytest
from fastapi.testclient import TestClient

from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.storage.projects import ProjectStorage
from app.models.project import ProjectCreate
from app.api.task_runs import router


@pytest.fixture
def tmp_ziya_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def project_id(tmp_ziya_home):
    storage = ProjectStorage(tmp_ziya_home)
    project = storage.create(
        ProjectCreate(name="test-project", path=str(tmp_ziya_home))
    )
    return project.id


@pytest.fixture
def client(tmp_ziya_home):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def executor_storage(tmp_ziya_home, project_id):
    """The instance the launch path's ``_run`` closure would hold."""
    from app.utils.paths import get_project_dir
    return TaskRunStorage(get_project_dir(project_id))


class TestCancelLiveness:
    def test_live_executor_gets_soft_cancel(self, client, project_id, executor_storage):
        run = executor_storage.create(TaskRunCreate(card_id="c1"))
        executor_storage.update_status(run.id, "running")
        executor_storage.mark_active(run.id)  # a different instance than the endpoint's
        try:
            resp = client.post(
                f"/api/v1/projects/{project_id}/task-runs/{run.id}/cancel"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["cancel_requested"] is True
            # Soft path: the executor owns the terminal transition.
            assert data["status"] == "running"
            assert data["completed_at"] is None
            on_disk = executor_storage.get(run.id)
            assert on_disk.status == "running"
        finally:
            executor_storage.mark_inactive(run.id)

    def test_dead_executor_gets_force_cancel(self, client, project_id, executor_storage):
        run = executor_storage.create(TaskRunCreate(card_id="c1"))
        executor_storage.update_status(run.id, "running")
        # Never claimed: a zombie row with no live owner anywhere.
        resp = client.post(
            f"/api/v1/projects/{project_id}/task-runs/{run.id}/cancel"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["cancel_requested"] is True
        assert data["completed_at"] is not None

    def test_released_executor_gets_force_cancel(self, client, project_id, executor_storage):
        run = executor_storage.create(TaskRunCreate(card_id="c1"))
        executor_storage.update_status(run.id, "running")
        executor_storage.mark_active(run.id)
        executor_storage.mark_inactive(run.id)  # owner exited cleanly, row left live
        resp = client.post(
            f"/api/v1/projects/{project_id}/task-runs/{run.id}/cancel"
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
