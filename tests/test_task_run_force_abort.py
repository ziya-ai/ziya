"""Force-stopping a task run that is stuck inside a block.

Soft-cancel (``cancel_requested``) is honored only at block boundaries —
between Repeat iterations, sequence siblings, Until loops.  A run whose
in-flight Task invocation never returns (a hung tool call, a model stream
that stalls without erroring) never reaches a boundary, so the flag is
set and never read.  That is the run that sat "running" for eight hours
with the cancel button doing nothing: the launch path dropped the
``asyncio.Task`` handle, so there was no in-process lever left to pull.

The fix has three seams, and the tests here cover each one AND the joins:

  * ``app.agents.live_runs`` keeps the coroutine handle per run so it can
    be cancelled in place, and records the intent so the coroutine can
    tell a force-stop from an event-loop shutdown.
  * ``TaskRunStorage.mark_aborted`` writes the run as ``held`` with reason
    ``user_abort`` at the innermost running block — the same record the
    recovery banner already turns into "resume from here" — and sets
    ``cancel_requested`` so any executor that outlives the write still
    stops at its next boundary.  Idempotent, because both the endpoint
    and the coroutine's own handler call it.
  * ``POST /cancel?force=true`` drives the two above.

The end-to-end class runs the REAL launch endpoint with a model stream
that hangs forever, proves that plain cancel cannot land, then proves
force does — and that the executor lock is released afterwards, which is
the observable difference between "unwound" and "zombie".
"""

import asyncio
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import live_runs
from app.models.task_run import (
    TaskRun, TaskRunBlockState, TaskRunCreate, TERMINAL_RUN_STATUSES,
)
from app.storage.task_runs import TaskRunStorage


# ── fixtures shared by the API classes ────────────────────────────────

@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-proj-force-abort"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": "Force Abort",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return project_id


@pytest.fixture
def runs_client(ziya_home, project_dir):
    """Only the task-runs router: exercises the endpoint's own fallbacks."""
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}), \
         patch("app.api.task_runs.get_ziya_home", return_value=ziya_home), \
         patch("app.api.task_runs.get_project_dir",
               return_value=ziya_home / "projects" / project_dir):
        from app.api.task_runs import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), project_dir, ziya_home


@pytest.fixture
def storage(ziya_home, project_dir):
    return TaskRunStorage(ziya_home / "projects" / project_dir)


def _seed_stuck_run(storage: TaskRunStorage) -> tuple[str, str, str]:
    """A running run whose group started first and whose leaf task started
    later — the shape the stuck GFX Stage 2 run had on disk."""
    run = storage.create(TaskRunCreate(card_id="card-x"))
    storage.update_status(run.id, "running")
    storage.set_block_state(run.id, TaskRunBlockState(
        block_id="b-group", block_type="group", status="running",
        started_at=1000,
    ))
    storage.set_block_state(run.id, TaskRunBlockState(
        block_id="b-leaf", block_type="task", status="running",
        started_at=2000,
    ))
    return run.id, "b-group", "b-leaf"


# ── live_runs registry ────────────────────────────────────────────────

class TestLiveRunsRegistry:
    @pytest.mark.asyncio
    async def test_abort_cancels_registered_task_and_records_intent(self):
        started = asyncio.Event()

        async def hang():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(hang())
        live_runs.register("r1", task)
        await started.wait()
        assert live_runs.get("r1") is task

        found = await live_runs.abort("r1", wait_s=2.0)
        assert found is True
        assert task.cancelled()
        # Intent is recorded for the coroutine's handler, and consumed once.
        assert live_runs.consume_abort("r1") is True
        assert live_runs.consume_abort("r1") is False
        # A finished task is no longer reported as live.
        assert live_runs.get("r1") is None
        live_runs.unregister("r1")

    @pytest.mark.asyncio
    async def test_abort_unknown_run_is_false_and_records_nothing(self):
        assert await live_runs.abort("nobody") is False
        assert live_runs.consume_abort("nobody") is False

    @pytest.mark.asyncio
    async def test_done_task_is_pruned_on_get(self):
        async def quick():
            return 1
        task = asyncio.create_task(quick())
        await task
        live_runs.register("r2", task)
        assert live_runs.get("r2") is None


# ── storage.mark_aborted ──────────────────────────────────────────────

class TestMarkAborted:
    def test_holds_at_innermost_running_block_and_sets_cancel_flag(self, storage):
        run_id, _group, leaf = _seed_stuck_run(storage)
        out = storage.mark_aborted(run_id)
        assert out.status == "held"
        assert out.held_reason == "user_abort"
        assert out.held_at_block_id == leaf
        assert out.cancel_requested is True
        assert out.completed_at is not None
        # Persisted, not just returned.
        fresh = storage.get(run_id)
        assert (fresh.status, fresh.held_reason, fresh.held_at_block_id) == (
            "held", "user_abort", leaf)

    def test_custom_error_text_is_recorded(self, storage):
        run_id, *_ = _seed_stuck_run(storage)
        out = storage.mark_aborted(run_id, error="stopped by test")
        assert out.error == "stopped by test"

    @pytest.mark.parametrize("terminal", sorted(TERMINAL_RUN_STATUSES))
    def test_noop_on_terminal_run(self, storage, terminal):
        run = storage.create(TaskRunCreate(card_id="c"))
        if terminal == "held":
            storage.mark_held(run.id, reason="throttling_error", block_id="b-x")
        else:
            storage.update_status(run.id, terminal)
        before = storage.get(run.id)
        out = storage.mark_aborted(run.id)
        assert out.status == terminal
        # The first writer's record survives: reason and block untouched.
        assert out.held_reason == before.held_reason
        assert out.held_at_block_id == before.held_at_block_id
        assert out.cancel_requested is False

    def test_missing_run_is_none(self, storage):
        assert storage.mark_aborted("missing") is None


# ── POST /cancel?force=true without a live coroutine ──────────────────

class TestForceCancelEndpoint:
    def test_force_on_zombie_holds_at_block_not_cancelled(self, runs_client, storage):
        tc, pid, _ = runs_client
        run_id, _group, leaf = _seed_stuck_run(storage)
        resp = tc.post(f"/api/v1/projects/{pid}/task-runs/{run_id}/cancel?force=true")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "held"
        assert body["held_reason"] == "user_abort"
        assert body["held_at_block_id"] == leaf
        assert body["cancel_requested"] is True

    def test_plain_cancel_on_zombie_still_marks_cancelled(self, runs_client, storage):
        # Unchanged behaviour: the zombie path without ``force`` keeps its
        # existing ``cancelled`` verdict.
        tc, pid, _ = runs_client
        run_id, *_ = _seed_stuck_run(storage)
        resp = tc.post(f"/api/v1/projects/{pid}/task-runs/{run_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        assert resp.json()["held_reason"] is None

    def test_force_on_terminal_is_idempotent(self, runs_client, storage):
        tc, pid, _ = runs_client
        run = storage.create(TaskRunCreate(card_id="c"))
        storage.update_status(run.id, "done")
        resp = tc.post(f"/api/v1/projects/{pid}/task-runs/{run.id}/cancel?force=true")
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        assert resp.json()["cancel_requested"] is False

    def test_force_missing_run_404(self, runs_client):
        tc, pid, _ = runs_client
        resp = tc.post(f"/api/v1/projects/{pid}/task-runs/missing/cancel?force=true")
        assert resp.status_code == 404


# ── end to end: real launch, hung stream, force-stop ──────────────────

class _HangingExecutor:
    """Streams one line of progress, then never yields again.  This is
    the shape of the stuck runs on disk: a model note, then silence."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        yield {"type": "text", "content": "starting build verification in frontend/"}
        await asyncio.Event().wait()   # never set
        yield {"type": "stream_end"}   # pragma: no cover — unreachable


@pytest.fixture
def launch_client(ziya_home, project_dir):
    """Both routers on one app, inside ``with`` so the TestClient keeps a
    single event loop across requests: the launch endpoint spawns the run
    on that loop and the cancel endpoint has to find the same task."""
    with patch.dict(os.environ, {
            "ZIYA_HOME": str(ziya_home),
            "ZIYA_SKIP_LAUNCH_PREFLIGHT": "1",
         }), \
         patch("app.api.task_cards.get_ziya_home", return_value=ziya_home), \
         patch("app.api.task_cards.get_project_dir",
               return_value=ziya_home / "projects" / project_dir), \
         patch("app.api.task_runs.get_ziya_home", return_value=ziya_home), \
         patch("app.api.task_runs.get_project_dir",
               return_value=ziya_home / "projects" / project_dir), \
         patch("app.streaming_tool_executor.StreamingToolExecutor",
               _HangingExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake",
                             "endpoint": "fake-not-bedrock"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
               return_value=[]):
        from app.api.task_cards import router as cards_router
        from app.api.task_runs import router as runs_router
        app = FastAPI()
        app.include_router(cards_router)
        app.include_router(runs_router)
        with TestClient(app) as tc:
            yield tc, project_dir


def _wait_for(storage, run_id, pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = storage.get(run_id)
        if run is not None and pred(run):
            return run
        time.sleep(0.02)
    return storage.get(run_id)


class TestForceAbortEndToEnd:
    def test_plain_cancel_cannot_land_but_force_holds_and_unwinds(
        self, launch_client, storage,
    ):
        tc, pid = launch_client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "Stuck", "root": {
                "block_type": "task", "name": "Stage 1",
                "instructions": "build it",
            }},
        ).json()
        resp = tc.post(f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch", json={})
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["id"]

        # The executor is inside the block: running, with a running task
        # block state and the progress note the model streamed.
        run = _wait_for(storage, run_id, lambda r: (
            r.status == "running"
            and any(s.status == "running" and s.block_type == "task"
                    for s in r.block_states.values())
            and r.progress_note is not None
        ))
        assert run.status == "running", run
        leaf = next(b for b, s in run.block_states.items()
                    if s.status == "running" and s.block_type == "task")
        assert storage.executor_alive(run_id), "executor lock should be held"

        # Plain cancel: the flag lands, the run does not stop.  This is
        # the eight-hour run.
        r1 = tc.post(f"/api/v1/projects/{pid}/task-runs/{run_id}/cancel")
        assert r1.status_code == 200
        assert r1.json()["cancel_requested"] is True
        assert r1.json()["status"] == "running"
        time.sleep(0.3)
        assert storage.get(run_id).status == "running"
        assert storage.executor_alive(run_id)

        # Force: interrupts the coroutine, holds at the leaf.
        r2 = tc.post(f"/api/v1/projects/{pid}/task-runs/{run_id}/cancel?force=true")
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["status"] == "held"
        assert body["held_reason"] == "user_abort"
        assert body["held_at_block_id"] == leaf
        assert body["cancel_requested"] is True

        # The coroutine actually unwound: lock released, registry empty.
        # A held record over a still-live executor would be a zombie
        # with a nicer label.
        deadline = time.time() + 3.0
        while time.time() < deadline and storage.executor_alive(run_id):
            time.sleep(0.02)
        assert not storage.executor_alive(run_id)
        assert live_runs.get(run_id) is None

        # And the hold is the resumable kind: the card snapshot the
        # resume endpoint requires is on the record.
        fresh = storage.get(run_id)
        assert fresh.status == "held"
        assert fresh.card_snapshot is not None

    def test_force_is_idempotent_after_the_coroutine_recorded_the_hold(
        self, launch_client, storage,
    ):
        tc, pid = launch_client
        card = tc.post(
            f"/api/v1/projects/{pid}/task-cards",
            json={"name": "Stuck2", "root": {
                "block_type": "task", "name": "Stage 1", "instructions": "x",
            }},
        ).json()
        run_id = tc.post(
            f"/api/v1/projects/{pid}/task-cards/{card['id']}/launch", json={},
        ).json()["id"]
        _wait_for(storage, run_id, lambda r: r.status == "running"
                  and any(s.status == "running" for s in r.block_states.values()))
        first = tc.post(f"/api/v1/projects/{pid}/task-runs/{run_id}/cancel?force=true").json()
        second = tc.post(f"/api/v1/projects/{pid}/task-runs/{run_id}/cancel?force=true").json()
        assert first["status"] == second["status"] == "held"
        assert first["held_at_block_id"] == second["held_at_block_id"]
        assert first["completed_at"] == second["completed_at"]
