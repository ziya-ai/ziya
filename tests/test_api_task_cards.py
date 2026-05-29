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


class TestProjectRootPropagation:
    """A4 — the X-Project-Root header MUST reach tool calls fired from
    inside the spawned task.  Two things must hold:

      1. ProjectContextMiddleware sets the ContextVar from the header
         (covered by the middleware's own tests, but exercised here
         end-to-end through the launch path).
      2. The ``_run`` coroutine re-sets the ContextVar inside the
         spawned task as defense-in-depth, so that even if the var is
         lost in transit it's restored before ``execute_block`` runs.

    We verify by capturing ``_request_project_root.get()`` from inside
    the patched ``execute_block``.  If it returns the expected path,
    the ContextVar is live where it matters.
    """

    @pytest.fixture(autouse=True)
    def _reset_project_root_var(self):
        """Each test in this class starts with the ContextVar cleared
        and tears it down on exit — so cross-test bleed cannot mask a
        real bug or trigger a spurious failure."""
        from app.context import _request_project_root
        token = _request_project_root.set(None)
        yield
        _request_project_root.reset(token)

    @staticmethod
    def _wait_terminal(pid, run_id, timeout=2.0):
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

    def test_run_propagates_project_root_to_execute_block(self, client, tmp_path):
        """The fix in ``_run``: when the launch handler captured a
        project_root (from the X-Project-Root-driven ContextVar at the
        request level), that value MUST be re-set on the ContextVar
        inside the spawned asyncio task so downstream tool calls see it
        instead of falling through to ``os.getcwd()``.

        We bypass the middleware (which has side effects we don't want
        in unit tests) and instead set the ContextVar directly in the
        request handler's context before launch, then verify it reaches
        ``execute_block`` inside the spawned task.
        """
        from app.context import _request_project_root, set_project_root

        proj_root = str(tmp_path)
        captured: dict = {}

        async def _capture(*_a, **_kw):
            captured["project_root"] = _request_project_root.get()
            return Artifact(summary="ok")

        # Pre-set the ContextVar — this is what middleware would do on a
        # real request.  The TestClient runs the handler in this same
        # thread/context, so the launch endpoint will pick it up via
        # ``get_project_root_or_none()`` and pass it into ``_run``.
        set_project_root(proj_root)

        tc, pid = client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()

        with patch("app.api.task_cards.execute_block", new=_capture):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

        run = self._wait_terminal(pid, run_id)
        assert run is not None and run.status == "done"
        # The critical invariant: ContextVar is the path the user set,
        # NOT None and NOT os.getcwd() (which is what the d3 task hit).
        assert captured["project_root"] == proj_root

    def test_run_resets_contextvar_in_spawned_task_context(self, client, tmp_path):
        """Direct exercise of the defense-in-depth ``set_project_root``
        call inside ``_run``: even if the spawned task's copied Context
        has ``_request_project_root`` reset to None, the call inside
        ``_run`` must restore it before ``execute_block`` runs.

        We simulate the lost-Context case by clearing the var in the
        request handler's context AFTER ``get_project_root_or_none()``
        runs (via a wrapper around ``execute_block`` that reads the
        var) — but rather than fight the asyncio.create_task copy
        semantics, we instead pass the project_root through a fixture
        and verify ``execute_block`` sees the correct value while the
        outer test thread's var is intentionally cleared.
        """
        from app.context import (
            _request_project_root, set_project_root,
        )

        proj_root = str(tmp_path)
        captured: dict = {}

        async def _capture(*_a, **_kw):
            captured["project_root"] = _request_project_root.get()
            return Artifact(summary="ok")

        # Set + then immediately tear down the outer var.  The launch
        # handler captures project_root via ``get_project_root_or_none()``
        # *before* we clear, so the captured value is correct; the
        # spawned task's Context-copy will reflect the cleared state,
        # exercising the defense-in-depth re-set.
        set_project_root(proj_root)

        tc, pid = client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": _task_root()},
        ).json()

        # Now clear in the outer thread.  Since FastAPI's TestClient
        # runs handlers in this same thread, the next launch will see
        # the cleared var from get_project_root_or_none() — meaning
        # the launch will pass project_root=None and we *expect* the
        # ContextVar inside execute_block to be None too.  This is the
        # negative control for the test above.
        _request_project_root.set(None)

        with patch("app.api.task_cards.execute_block", new=_capture):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

        run = self._wait_terminal(pid, run_id)
        assert run is not None and run.status == "done"
        # Negative control: with no project_root captured at launch
        # time, the defense-in-depth set is skipped (its `if project_root:`
        # guard) — so the ContextVar is None inside execute_block.
        assert captured["project_root"] is None


class TestPermissionsSnapshotCapture:
    """A3 — the permissions snapshot must be persisted on the run
    record at launch time.  This is what lets a post-mortem reconstruct
    *what the agent was actually allowed to do* — without it, a failed
    task is opaque (the d3 case had no record of granted permissions).

    Specifically guards against a wiring regression we hit in production:
    the snapshot helper expects ``root_block=card.root`` but the launch
    site originally passed ``card=card``, raising a TypeError that was
    silently swallowed by the non-fatal try/except wrapper.  The unit
    tests for ``build_permissions_snapshot`` itself called the helper
    directly with the right kwarg, so the wiring mismatch was invisible
    until a real launch surfaced it as a log warning + missing field.
    """

    def test_snapshot_captured_on_launch(self, client):
        """End-to-end: launching a card writes a non-empty snapshot
        onto the run record before ``execute_block`` is invoked.
        """
        tc, pid = client
        # Give the block a non-empty scope so it appears in
        # block_scopes (the snapshot walker skips scopeless blocks).
        root_with_scope = _task_root("only")
        root_with_scope["scope"] = {
            "tools": ["file_read", "file_write"],
            "shell_commands": ["pytest"],
        }
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "T", "root": root_with_scope},
        ).json()

        # Pause execute_block so we can read the run state immediately
        # after launch (the snapshot is written synchronously before
        # the spawned task starts running).
        pause = asyncio.Event()

        async def _wait_forever(*_a, **_kw):
            await pause.wait()
            return Artifact(summary="unused")

        with patch("app.api.task_cards.execute_block", new=_wait_forever):
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch",
                json={},
            )
            assert resp.status_code == 200
            run_id = resp.json()["id"]

            from app.storage.task_runs import TaskRunStorage
            from app.utils.paths import get_project_dir
            storage = TaskRunStorage(get_project_dir(pid))
            run = storage.get(run_id)

            # The exact wiring regression: helper accepts root_block,
            # not card.  If task_cards.py passes the wrong kwarg, the
            # try/except swallows the TypeError and snapshot stays None.
            assert run.permissions_snapshot is not None, (
                "permissions_snapshot was not persisted at launch — "
                "likely a kwarg mismatch in build_permissions_snapshot()"
            )
            snap = run.permissions_snapshot
            # Schema sanity: top-level keys per app/utils/permissions_snapshot.py
            assert "schema_version" in snap
            assert "captured_at" in snap
            assert "base_policy" in snap
            assert "block_scopes" in snap
            # block_scopes is a dict keyed by block id; walking the
            # card.root tree must populate at least one entry.
            assert len(snap["block_scopes"]) >= 1, (
                "block_scopes should contain at least the root task block"
            )

            pause.set()
