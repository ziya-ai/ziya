"""
Terminal-status guards on the run control endpoints.

``cancel`` / ``pause`` / ``resume`` / ``step`` each begin with a guard that
returns the run unchanged when it is already terminal — there is no
executor left to signal.  Each guard carried its OWN hardcoded list:

    if run.status in ("done", "failed", "cancelled"):

``RunStatus`` has eight members, and two of the terminal ones are absent
from that tuple: ``partial`` (derived at the terminal write when a run made
progress but left work unfinished) and ``held`` (stopped by an
infrastructure fault).  A run in either state falls THROUGH the guard, and
the endpoint then signals an executor that has already unwound.

The concrete damage, measured:

  * ``cancel`` on a held run finds ``is_active()`` False and takes the
    "no live executor — force the terminal state directly" path, so the
    status is overwritten to ``cancelled``.  ``held_reason`` and
    ``held_at_block_id`` survive as fields, which is worse than losing
    them: the record now claims the user cancelled the run while still
    carrying the infrastructure fault that actually stopped it, and the
    "this was not your card's fault, resume it" signal is gone.
  * ``pause`` sets ``pause_requested = True`` on a dead run.
  * ``step`` sets ``pause_requested`` AND grants step credits to nothing.

The frontend does not currently offer these controls on a held run
(``runControls.TERMINAL`` includes it), but the endpoints are reachable
from the API, the CLI, and any stale browser tab, so the guard cannot
delegate its correctness to the UI.

Root cause is duplication, not any single wrong tuple: five places
independently decide what "terminal" means, and
``TaskRunStorage.update_status`` already lists all five statuses while
these four list three.  The fix is one shared definition.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_run import RunStatus, TaskRunCreate
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-guards"
    proj = ziya_home / "projects" / project_id
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({
        "id": project_id,
        "name": "Test",
        "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return project_id


@pytest.fixture
def client(ziya_home, project_dir):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}), \
         patch("app.api.task_runs.get_ziya_home", return_value=ziya_home), \
         patch("app.api.task_runs.get_project_dir",
               return_value=ziya_home / "projects" / project_dir):
        from app.api.task_runs import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), project_dir, ziya_home


@pytest.fixture
def storage(client):
    _, pid, home = client
    return TaskRunStorage(home / "projects" / pid)


def _base(pid):
    return f"/api/v1/projects/{pid}/task-runs"


def _held_run(storage):
    run = storage.create(TaskRunCreate(card_id="c1"))
    storage.update_status(run.id, "running")
    storage.mark_held(
        run.id, reason="connection_error", block_id="b-bf007c11",
        error="Could not connect to the endpoint URL",
    )
    return run.id


def _partial_run(storage):
    run = storage.create(TaskRunCreate(card_id="c1"))
    storage.update_status(run.id, "running")
    storage.update_status(run.id, "partial")
    return run.id


# ── one shared definition of "terminal" ──────────────────────────────

class TestTerminalStatusConstant:
    """The duplication is the defect; a single exported set is the fix."""

    def test_constant_exists(self):
        from app.models.task_run import TERMINAL_RUN_STATUSES
        assert TERMINAL_RUN_STATUSES

    def test_live_and_terminal_partition_runstatus(self):
        # Both sets are ENUMERATED in the model, not derived from each
        # other: deriving one as the complement of the other would make
        # this assertion tautological and unable to fail.  Enumerated, it
        # catches the actual mistake — a status added to RunStatus and
        # then classified nowhere, which is how 'partial' and 'held' came
        # to fall through four separate endpoint guards.
        import typing
        from app.models.task_run import (
            LIVE_RUN_STATUSES, TERMINAL_RUN_STATUSES,
        )
        all_statuses = set(typing.get_args(RunStatus))
        assert set(LIVE_RUN_STATUSES) | set(TERMINAL_RUN_STATUSES) == all_statuses, (
            "every RunStatus must be classified as live or terminal; "
            f"unclassified: {all_statuses - set(LIVE_RUN_STATUSES) - set(TERMINAL_RUN_STATUSES)}"
        )
        assert set(LIVE_RUN_STATUSES) & set(TERMINAL_RUN_STATUSES) == set(), (
            "a status cannot be both live and terminal"
        )

    def test_paused_is_not_terminal(self):
        # 'paused' is a LIVE hold: the executor is parked in
        # _wait_if_paused and will resume.  Calling it terminal would make
        # resume a no-op and strand every paused run.
        from app.models.task_run import TERMINAL_RUN_STATUSES
        assert "paused" not in TERMINAL_RUN_STATUSES

    def test_held_and_partial_are_terminal(self):
        from app.models.task_run import TERMINAL_RUN_STATUSES
        assert "held" in TERMINAL_RUN_STATUSES
        assert "partial" in TERMINAL_RUN_STATUSES


# ── cancel must not rewrite a held run's outcome ─────────────────────

class TestCancelOnTerminalRun:
    def test_cancel_does_not_overwrite_held(self, client, storage):
        tc, pid, _ = client
        run_id = _held_run(storage)
        resp = tc.post(f"{_base(pid)}/{run_id}/cancel")
        assert resp.status_code == 200, resp.text
        after = storage.get(run_id)
        assert after.status == "held", (
            f"cancel rewrote a held run to {after.status!r}; the record now "
            f"claims the user stopped it while still carrying "
            f"held_reason={after.held_reason!r}"
        )

    def test_cancel_preserves_resume_coordinates(self, client, storage):
        tc, pid, _ = client
        run_id = _held_run(storage)
        tc.post(f"{_base(pid)}/{run_id}/cancel")
        after = storage.get(run_id)
        assert after.held_reason == "connection_error"
        assert after.held_at_block_id == "b-bf007c11"

    def test_cancel_does_not_overwrite_partial(self, client, storage):
        # Same defect, predating 'held': a partial run records real
        # completed work, and relabelling it "cancelled" hides that.
        tc, pid, _ = client
        run_id = _partial_run(storage)
        tc.post(f"{_base(pid)}/{run_id}/cancel")
        assert storage.get(run_id).status == "partial"

    def test_cancel_still_works_on_a_live_run(self, client, storage):
        # The positive half: the guard must not swallow a real cancel.
        tc, pid, _ = client
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        resp = tc.post(f"{_base(pid)}/{run.id}/cancel")
        assert resp.status_code == 200
        after = storage.get(run.id)
        assert after.status == "cancelled" or after.cancel_requested


# ── pause / resume / step must be inert on a terminal run ────────────

class TestPauseResumeStepOnTerminalRun:
    def test_pause_does_not_flag_a_held_run(self, client, storage):
        tc, pid, _ = client
        run_id = _held_run(storage)
        tc.post(f"{_base(pid)}/{run_id}/pause")
        after = storage.get(run_id)
        assert after.pause_requested is False, (
            "pause set pause_requested on a run whose executor has already "
            "unwound; nothing will ever observe the flag"
        )
        assert after.status == "held"

    def test_step_does_not_grant_credits_to_a_held_run(self, client, storage):
        tc, pid, _ = client
        run_id = _held_run(storage)
        tc.post(f"{_base(pid)}/{run_id}/step?count=3")
        after = storage.get(run_id)
        assert (after.step_budget or 0) == 0, (
            "step granted boundary credits to a dead executor"
        )
        assert after.pause_requested is False

    def test_resume_is_a_noop_on_a_held_run(self, client, storage):
        tc, pid, _ = client
        run_id = _held_run(storage)
        resp = tc.post(f"{_base(pid)}/{run_id}/resume")
        assert resp.status_code == 200
        assert storage.get(run_id).status == "held"

    def test_pause_does_not_flag_a_partial_run(self, client, storage):
        tc, pid, _ = client
        run_id = _partial_run(storage)
        tc.post(f"{_base(pid)}/{run_id}/pause")
        assert storage.get(run_id).pause_requested is False

    def test_pause_still_works_on_a_live_run(self, client, storage):
        # Positive half, so a guard that rejected everything would fail.
        tc, pid, _ = client
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        tc.post(f"{_base(pid)}/{run.id}/pause")
        assert storage.get(run.id).pause_requested is True

    def test_step_still_works_on_a_live_run(self, client, storage):
        tc, pid, _ = client
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        tc.post(f"{_base(pid)}/{run.id}/step?count=2")
        after = storage.get(run.id)
        assert after.step_budget == 2
        assert after.pause_requested is True
