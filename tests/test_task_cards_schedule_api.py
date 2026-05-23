"""
Tests for the schedule-state endpoint and for launching cards whose
root is a Schedule or Until block.

The schedule-state endpoint is a read-only window into
`<project>/schedule_state.json` — the file the in-process scheduler
writes when it fires a card.  The endpoint never produces records;
only the scheduler does.  These tests verify:

  - 404 for unknown card
  - {} for a scheduled card the scheduler hasn't fired yet
  - the persisted record passes through verbatim once present
  - {} for a card without any schedule block (no false positives)

The launch tests verify that POST /launch dispatches Schedule and
Until roots through execute_block, the same way Repeat/Parallel
already work.  We patch execute_block so the test doesn't make a
real model call.
"""

import asyncio
import json
import os
import time
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.task_card import Artifact


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-sched"
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
    """A TestClient bound to a temp ziya_home with one project on disk."""
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.task_cards.get_ziya_home", return_value=ziya_home):
            with patch(
                "app.api.task_cards.get_project_dir",
                return_value=ziya_home / "projects" / project_dir,
            ):
                # The scheduler module reads state via its own paths
                # helpers; patch get_project_dir there too so writes
                # and reads land in the same temp dir.
                with patch(
                    "app.agents.task_scheduler.get_project_dir",
                    return_value=ziya_home / "projects" / project_dir,
                ):
                    from app.api.task_cards import router

                    app = FastAPI()
                    app.include_router(router)
                    yield TestClient(app), project_dir, ziya_home


# ── Helpers ────────────────────────────────────────────────────────

def _task_root(instr: str = "do it") -> dict:
    return {
        "block_type": "task",
        "name": "T",
        "instructions": instr,
    }


def _schedule_root(body=None) -> dict:
    return {
        "block_type": "schedule",
        "name": "Sched",
        "schedule_mode": "interval",
        "schedule_interval_value": 1,
        "schedule_interval_unit": "hours",
        "schedule_enabled": True,
        "body": [body or _task_root("scheduled action")],
    }


def _until_root(body=None) -> dict:
    return {
        "block_type": "until",
        "name": "U",
        "until_mode": "model",
        "until_condition": "tests pass",
        "until_max": 3,
        "body": [body or _task_root("attempt fix")],
    }


def _create_card(client, project_id, root) -> str:
    resp = client.post(
        f"/api/v1/projects/{project_id}/task-cards",
        json={"name": "Card", "root": root},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ── schedule-state endpoint ────────────────────────────────────────

class TestScheduleStateEndpoint:
    def test_unknown_card_returns_404(self, client):
        tc, pid, _ = client
        resp = tc.get(
            f"/api/v1/projects/{pid}/task-cards/does-not-exist/schedule-state"
        )
        assert resp.status_code == 404

    def test_empty_state_for_unfired_scheduled_card(self, client):
        """Card has a schedule block but the scheduler has never written
        a state record yet → endpoint returns {} (not None, not 404)."""
        tc, pid, _ = client
        card_id = _create_card(tc, pid, _schedule_root())
        resp = tc.get(
            f"/api/v1/projects/{pid}/task-cards/{card_id}/schedule-state"
        )
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_returns_persisted_record(self, client):
        """When the scheduler has written a record, the endpoint returns
        it verbatim — block_id, fires_so_far, last_fire_at, run_ids."""
        tc, pid, ziya_home = client
        card_id = _create_card(tc, pid, _schedule_root())

        # Simulate the scheduler having fired once.  Write directly to
        # the state file the scheduler reads from.
        from app.agents import task_scheduler as ts
        state = {
            card_id: {
                "block_id": "s-abc",
                "next_fire_at": 1_700_000_000_000,
                "last_fire_at": 1_699_996_400_000,
                "fires_so_far": 1,
                "run_ids": ["run-xyz-1"],
            }
        }
        ts._write_state(pid, state)

        resp = tc.get(
            f"/api/v1/projects/{pid}/task-cards/{card_id}/schedule-state"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["block_id"] == "s-abc"
        assert body["fires_so_far"] == 1
        assert body["last_fire_at"] == 1_699_996_400_000
        assert body["run_ids"] == ["run-xyz-1"]

    def test_empty_state_for_card_without_schedule(self, client):
        """Card has no schedule block at all → endpoint returns {}.
        Specifically does NOT leak another card's state record."""
        tc, pid, _ = client
        plain_card_id = _create_card(tc, pid, _task_root())
        # Plant a record for a DIFFERENT card to make sure we don't
        # accidentally return any record at all.
        from app.agents import task_scheduler as ts
        ts._write_state(pid, {
            "some-other-card-id": {"fires_so_far": 99},
        })
        resp = tc.get(
            f"/api/v1/projects/{pid}/task-cards/{plain_card_id}/schedule-state"
        )
        assert resp.status_code == 200
        assert resp.json() == {}


# ── Launch routing for new block types ─────────────────────────────

class TestLaunchNewBlockTypes:
    def test_launch_schedule_root_dispatches_via_execute_block(self, client):
        """A card whose root is a schedule block can be launched via
        POST /launch.  The launch path dispatches through execute_block,
        which routes schedule → _execute_schedule_passthrough (one fire
        of the body).  We just verify the dispatch happened."""
        tc, pid, _ = client
        card_id = _create_card(tc, pid, _schedule_root())

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="ran scheduled body")),
        ) as mock_exec:
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card_id}/launch",
                json={},
            )
        assert resp.status_code == 200
        # Allow the background task to run.
        time.sleep(0.05)
        assert mock_exec.await_count == 1
        # The block passed to execute_block should be the schedule root,
        # not a synthesized child — passthrough is the executor's job.
        called_block = mock_exec.await_args.args[0]
        assert called_block.block_type == "schedule"

    def test_launch_until_root_dispatches_via_execute_block(self, client):
        """Same routing check for an Until-rooted card."""
        tc, pid, _ = client
        card_id = _create_card(tc, pid, _until_root())

        with patch(
            "app.api.task_cards.execute_block",
            new=AsyncMock(return_value=Artifact(summary="condition met")),
        ) as mock_exec:
            resp = tc.post(
                f"/api/v1/projects/{pid}/task-cards/{card_id}/launch",
                json={},
            )
        assert resp.status_code == 200
        time.sleep(0.05)
        assert mock_exec.await_count == 1
        called_block = mock_exec.await_args.args[0]
        assert called_block.block_type == "until"
