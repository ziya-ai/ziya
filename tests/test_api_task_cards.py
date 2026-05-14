"""
Tests for app.api.task_cards — the launch endpoint.

The other CRUD endpoints are thin passthroughs to TaskCardStorage,
which is already covered by test_task_card_storage.py.  These tests
focus on the launch path: it must seed block_states, dispatch via
execute_block (not the old Slice-C task-only executor), and convert
BlockExecutionCancelled into a cancelled run.
"""

import asyncio
import json
import os
import time
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact, Block


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-001"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    project_data = {
        "id": project_id,
        "name": "Test",
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
        with patch("app.api.task_cards.get_ziya_home", return_value=ziya_home):
            with patch(
                "app.api.task_cards.get_project_dir",
                return_value=ziya_home / "projects" / project_dir,
            ):
                from app.api.task_cards import router

                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_dir


def _task_root(instr: str = "do it") -> dict:
    return {
        "block_type": "task",
        "name": "T",
        "instructions": instr,
    }


def _repeat_root(count: int = 3) -> dict:
    return {
        "block_type": "repeat",
        "name": "L",
        "repeat_mode": "count",
        "repeat_count": count,
        "body": [_task_root("iter")],
    }


class TestLaunchValidation:
    def test_launch_missing_card_404(self, client):
        tc, pid = client
        resp = tc.post(
            f"/api/v1/projects/{pid}/task-cards/does-not-exist/launch",
            json={},
        )
        assert resp.status_code == 404

    def test_launch_accepts_task_root(self, client):
        """Single-task roots still launch (baseline from Slice C)."""
        tc, pid = client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="done")),
        ):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["card_id"] == card["id"]
        assert body["status"] in ("queued", "running", "done")

    def test_launch_accepts_repeat_root(self, client):
        """After Slice D: Repeat roots are no longer rejected."""
        tc, pid = client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "R", "root": _repeat_root(count=5)},
        ).json()

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="repeated")),
        ):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
        assert resp.status_code == 200

    def test_launch_accepts_parallel_root(self, client):
        tc, pid = client
        root = {
            "block_type": "parallel", "name": "P",
            "body": [_task_root("a"), _task_root("b")],
        }
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "P", "root": root},
        ).json()

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="par")),
        ):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
        assert resp.status_code == 200


class TestLaunchSeeding:
    def test_block_states_seeded_for_nested_tree(self, client):
        """_seed_block_states should populate run.block_states for every
        block in the tree before execution starts."""
        tc, pid = client
        root = {
            "block_type": "repeat", "name": "outer",
            "repeat_mode": "count", "repeat_count": 2,
            "body": [
                _task_root("first"),
                {
                    "block_type": "repeat", "name": "inner",
                    "repeat_mode": "count", "repeat_count": 3,
                    "body": [_task_root("nested")],
                },
            ],
        }
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "Nested", "root": root},
        ).json()

        # Block execute_block so the run stays in the running state and
        # we can inspect the seed result without racing the async task.
        pause = asyncio.Event()

        async def _wait_forever(*_args, **_kwargs):
            await pause.wait()
            return Artifact(summary="unused")

        with patch("app.api.task_cards.execute_block", new=_wait_forever):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

            # Load the run directly from storage — bypasses the API so
            # we don't need the task_runs router mounted here.
            from app.storage.task_runs import TaskRunStorage
            from app.utils.paths import get_project_dir
            storage = TaskRunStorage(get_project_dir(pid))
            run = storage.get(run_id)
            # Four blocks: outer repeat, first task, inner repeat, nested task
            assert len(run.block_states) == 4
            # Release the waiting execute_block so the background task
            # completes and doesn't leak across tests.
            pause.set()


class TestLaunchCancellationHandling:
    def test_block_execution_cancelled_sets_cancelled_status(self, client):
        """When execute_block raises BlockExecutionCancelled, the run's
        final status should be 'cancelled' rather than 'failed'."""
        tc, pid = client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "X", "root": _task_root()},
        ).json()

        from app.agents.block_executor import BlockExecutionCancelled

        async def _raise_cancelled(*_a, **_k):
            raise BlockExecutionCancelled()

        with patch("app.api.task_cards.execute_block", new=_raise_cancelled):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

        # Poll briefly — the background task should settle status quickly.
        from app.storage.task_runs import TaskRunStorage
        from app.utils.paths import get_project_dir
        storage = TaskRunStorage(get_project_dir(pid))
        deadline = time.time() + 2.0
        run = None
        while time.time() < deadline:
            run = storage.get(run_id)
            if run and run.status != "running":
                break
            time.sleep(0.02)
        assert run is not None
        assert run.status == "cancelled"


class TestLifecycleEvents:
    """Run-level events (run_started / run_completed) emitted via the
    task_run_stream_relay.  Block-level and iteration-level events are
    covered by test_block_executor.py; these tests cover only the two
    events the launch endpoint itself emits."""

    @staticmethod
    def _install_relay_capture(monkeypatch):
        """Replace _relay.safe_push with a capturing stub.  Returns a
        list that will be populated with (run_id, event) tuples as
        events fire."""
        captured: list = []

        async def _stub(run_id, event):
            captured.append((run_id, event))

        monkeypatch.setattr(
            "app.api.task_cards._relay.safe_push", _stub,
        )
        return captured

    @staticmethod
    def _wait_for_run_terminal(pid: str, run_id: str, timeout: float = 2.0):
        from app.storage.task_runs import TaskRunStorage
        from app.utils.paths import get_project_dir
        storage = TaskRunStorage(get_project_dir(pid))
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = storage.get(run_id)
            if run and run.status in ("done", "failed", "cancelled"):
                return run
            time.sleep(0.02)
        return storage.get(run_id)

    def test_run_started_and_completed_on_success(self, client, monkeypatch):
        tc, pid = client
        captured = self._install_relay_capture(monkeypatch)
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="ok")),
        ):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

        run = self._wait_for_run_terminal(pid, run_id)
        assert run is not None and run.status == "done"

        events_for_run = [e for (rid, e) in captured if rid == run_id]
        types = [e["type"] for e in events_for_run]
        assert "run_started" in types
        assert "run_completed" in types
        # Completion event carries the final status.
        completed = next(e for e in events_for_run if e["type"] == "run_completed")
        assert completed["status"] == "done"
        # Ordering: started before completed.
        assert types.index("run_started") < types.index("run_completed")

    def test_run_completed_status_failed_when_artifact_failed(self, client, monkeypatch):
        """A root block that returns an artifact with failed=True should
        transition the run to 'failed', not 'done'."""
        tc, pid = client
        captured = self._install_relay_capture(monkeypatch)
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="nope", failed=True)),
        ):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            run_id = resp.json()["id"]

        run = self._wait_for_run_terminal(pid, run_id)
        assert run.status == "failed"

        completed = [e for (rid, e) in captured
                     if rid == run_id and e["type"] == "run_completed"]
        assert len(completed) == 1
        assert completed[0]["status"] == "failed"

    def test_run_completed_on_cancellation(self, client, monkeypatch):
        tc, pid = client
        captured = self._install_relay_capture(monkeypatch)
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()

        from app.agents.block_executor import BlockExecutionCancelled

        async def _raise_cancelled(*_a, **_k):
            raise BlockExecutionCancelled()

        with patch("app.api.task_cards.execute_block", new=_raise_cancelled):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            run_id = resp.json()["id"]

        run = self._wait_for_run_terminal(pid, run_id)
        assert run.status == "cancelled"

        completed = [e for (rid, e) in captured
                     if rid == run_id and e["type"] == "run_completed"]
        assert len(completed) == 1
        assert completed[0]["status"] == "cancelled"

    def test_relay_push_failure_does_not_break_run(self, client, monkeypatch):
        """If the relay raises, the run must still complete.  safe_push
        is already defensive; this verifies the invariant at the API
        level too."""
        tc, pid = client

        async def _boom(run_id, event):
            raise RuntimeError("relay is on fire")

        # Patch the underlying push, not safe_push — safe_push itself
        # is what we're verifying.
        monkeypatch.setattr("app.agents.task_run_stream_relay.push", _boom)

        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()
        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="ok")),
        ):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

        run = self._wait_for_run_terminal(pid, run_id)
        assert run is not None
        assert run.status == "done"  # relay failure didn't derail execution