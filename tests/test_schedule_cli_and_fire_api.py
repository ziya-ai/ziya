"""
The two surfaces that reach the registry from outside the GUI:
``POST …/schedules/{card}/fire`` and ``ziya schedule``.  Both go through
app/agents/schedule_registry.py and task_scheduler.fire_now, so the
assertions here are about the seam -- the wiring exists and reports the
registry's answer -- not about scheduling semantics (test_schedule_registry,
test_scheduler_run_parity).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import schedule_registry as reg
from app.models.project import ProjectCreate
from app.models.task_card import Artifact, Block, TaskCardCreate
from app.storage.projects import ProjectStorage
from app.storage.task_cards import TaskCardStorage
from app.utils.paths import get_project_dir, get_ziya_home


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    monkeypatch.setenv("ZIYA_SKIP_LAUNCH_PREFLIGHT", "1")
    return tmp_path


@pytest.fixture
def project(home):
    repo = home / "repo"
    repo.mkdir()
    return ProjectStorage(get_ziya_home()).create(ProjectCreate(path=str(repo), name="repo"))


def _card(project, name="nightly", **cadence):
    cadence.setdefault("schedule_mode", "daily_at")
    cadence.setdefault("schedule_daily_at", "02:00")
    root = Block(block_type="schedule", id="s1", name=name,
                 body=[Block(block_type="task", id="t1", name="t", instructions="x")], **cadence)
    return TaskCardStorage(get_project_dir(project.id)).create(
        TaskCardCreate(name=name, description="", root=root))


# -- API -----------------------------------------------------------------------

def test_fire_endpoint_starts_a_counted_headless_run(home, project):
    from app.api import schedules as api
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    card = _card(project)
    from app.agents import task_scheduler as ts

    async def fake_exec(block, ctx):
        return Artifact(summary="ok")
    with patch("app.agents.block_executor.execute_block", side_effect=fake_exec):
        r = client.post(f"/api/v1/projects/{project.id}/schedules/{card.id}/fire")
    assert r.status_code == 202, r.text
    assert r.json()["card_id"] == card.id
    assert ts._read_state(project.id)[card.id]["fires_so_far"] == 1
    assert client.post(f"/api/v1/projects/{project.id}/schedules/nope/fire").status_code == 404


def test_real_server_mounts_fire_route():
    from app.server import app as server_app
    paths = {getattr(r, "path", "") for r in server_app.routes}
    assert "/api/v1/projects/{project_id}/schedules/{card_id}/fire" in paths


# -- CLI -----------------------------------------------------------------------

def test_schedule_is_a_top_level_command():
    from app.cli_commands import TOP_LEVEL_COMMANDS
    assert "schedule" in TOP_LEVEL_COMMANDS
    from app.cli import create_parser
    args = create_parser().parse_args(["schedule", "register", "nightly", "--daily", "03:15"])
    assert args.schedule_cmd == "register" and args.card == "nightly" and args.daily == "03:15"
    args = create_parser().parse_args(["schedule", "register", "x", "--every", "2", "hours"])
    assert args.every == ["2", "hours"]
    assert create_parser().parse_args(["schedule", "list"]).schedule_cmd == "list"


def test_cli_register_list_disable_unregister(home, project, capsys):
    from app import cli_schedule
    import argparse
    card = _card(project)
    root = project.path
    ns = lambda **kw: argparse.Namespace(**{"daily": None, "every": None, "cron": None, "at": None, **kw})

    assert cli_schedule.cmd_list(root) == 0
    out = capsys.readouterr().out
    assert "not registered" in out and "nightly" in out

    assert cli_schedule.cmd_mutate(root, "register", "nightly", ns(daily="03:15")) == 0
    assert reg.get_registration(project.id, card.id).schedule_daily_at == "03:15"
    assert cli_schedule.cmd_mutate(root, "register", "nightly", ns()) == 1       # already
    assert cli_schedule.cmd_mutate(root, "register", "missing", ns()) == 1       # no card

    assert cli_schedule.cmd_list(root) == 0
    out = capsys.readouterr().out
    assert "daily at 03:15" in out and "Registered:" in out

    assert cli_schedule.cmd_mutate(root, "disable", card.id, ns()) == 0
    assert reg.get_registration(project.id, card.id).enabled is False
    assert cli_schedule.cmd_mutate(root, "enable", card.id, ns()) == 0
    assert reg.get_registration(project.id, card.id).enabled is True

    # A bad cadence is refused with the registry's reason and exit 2.
    assert cli_schedule.cmd_mutate(root, "unregister", card.id, ns()) == 0
    assert cli_schedule.cmd_mutate(root, "register", card.id, ns(cron="0 3 * *")) == 2
    assert "5 fields" in capsys.readouterr().err
    assert reg.get_registration(project.id, card.id) is None


def test_cli_list_names_a_registration_that_cannot_fire(home, project, capsys):
    from app import cli_schedule
    card = _card(project)
    reg.register(project.id, card)
    regs = reg.read_registry(project.id)
    regs[card.id] = regs[card.id].model_copy(update={"schedule_mode": "cron", "schedule_cron": "x"})
    reg.write_registry(project.id, regs)
    assert cli_schedule.cmd_list(project.path) == 0
    assert "cannot fire" in capsys.readouterr().out


def test_cli_report_fire_exit_codes(home, project, capsys):
    from app import cli_schedule
    from app.agents import task_scheduler as ts
    card = _card(project)

    async def go(exc):
        with patch("app.agents.block_executor.execute_block", side_effect=exc):
            rid = await ts.fire_now(project.id, card.id)
            from app.agents import live_runs
            t = live_runs.get(rid)
            if t is not None:
                await t
            return rid

    async def ok(block, ctx):
        return Artifact(summary="ok")
    rid = asyncio.run(go(ok))
    assert cli_schedule.report_fire(project.id, card.name, rid) == 0
    assert "done" in capsys.readouterr().out

    async def bad(block, ctx):
        return Artifact(summary="boom", failed=True)
    rid = asyncio.run(go(bad))
    assert cli_schedule.report_fire(project.id, card.name, rid) == 1
