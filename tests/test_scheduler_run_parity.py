"""
A scheduled fire is a first-class run (app/agents/run_driver.py).

Before the shared driver the scheduler's _fire_one had its own thin copy
of the launch endpoint's run loop, so a scheduled run:

  * never took the executor lock -> Cancel wrote ``cancelled`` over a run
    whose executor kept going, and a sibling server's startup reconcile
    marked a LIVE scheduled run ``held``;
  * recorded the headless unsigned-escalation TaskInfraError as ``failed``;
  * emitted no run_started / run_completed on the relay.

Also covered here: fire_now (fire through the scheduler path on demand),
schedule_timezone, the editor-aligned defaults, cadence validation at the
registry / API / validator / tick seams.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import schedule_registry as reg
from app.agents import task_scheduler as ts
from app.agents.task_executor import TaskInfraError
from app.models.project import ProjectCreate
from app.models.task_card import Artifact, Block, TaskCardCreate
from app.storage.projects import ProjectStorage
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage
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


def _sched_block(**cadence) -> Block:
    cadence.setdefault("schedule_mode", "interval")
    cadence.setdefault("schedule_interval_value", 1)
    cadence.setdefault("schedule_interval_unit", "hours")
    return Block(block_type="schedule", id="s1", name="nightly",
                 body=[Block(block_type="task", id="t1", name="t", instructions="x")],
                 **cadence)


def _card(project, root, name="c"):
    return TaskCardStorage(get_project_dir(project.id)).create(
        TaskCardCreate(name=name, description="", root=root))


def _target(project, card):
    return ts._ScheduledCard(project_id=project.id, card_id=card.id, card_name=card.name,
                             block=card.root, body_root=ts._resolve_body_root(card.root))


async def _settle(n=30):
    for _ in range(n):
        await asyncio.sleep(0)


# -- the driver seam -----------------------------------------------------------

def test_fire_one_goes_through_the_shared_driver(home, project):
    """The scheduler must not keep its own copy of the run loop."""
    card = _card(project, _sched_block())
    seen = {}

    def fake_start(launch):
        seen["launch"] = launch
        return None

    async def go():
        with patch("app.agents.run_driver.start_run", side_effect=fake_start):
            await ts._fire_one(_target(project, card))
    asyncio.run(go())
    launch = seen["launch"]
    assert launch.label == "Scheduled run"
    assert launch.root.id == "t1"
    assert launch.context_kwargs["scope_stack"] == [card.root.scope]
    assert launch.project_path == project.path


def test_scheduled_run_holds_executor_lock_while_running(home, project):
    """Cancel on a scheduled run used to take the zombie path because no
    lock was held.  A fresh TaskRunStorage (as the cancel endpoint builds)
    must see the executor as alive for the whole run."""
    card = _card(project, _sched_block())
    probe = TaskRunStorage(get_project_dir(project.id))
    seen = {}
    gate = asyncio.Event()

    async def fake_exec(block, ctx):
        seen["alive_during"] = probe.executor_alive(ctx.run_id)
        await gate.wait()
        return Artifact(summary="ok")

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec):
            run_id = await ts._fire_one(_target(project, card))
            await _settle()
            assert probe.executor_alive(run_id), "lock not held mid-run"
            gate.set()
            await _settle(60)
            seen["alive_after"] = probe.executor_alive(run_id)
            seen["run_id"] = run_id
    asyncio.run(go())
    assert seen["alive_during"] is True
    assert seen["alive_after"] is False
    assert probe.get(seen["run_id"]).status == "done"


def test_scheduled_run_infra_fault_is_held_not_failed(home, project):
    """The headless unsigned-escalation hold the executor raises on purpose
    came back as ``failed`` from the scheduler's own except clause."""
    card = _card(project, _sched_block())

    async def fake_exec(block, ctx):
        raise TaskInfraError("needs a signature", infra_kind="unsigned_escalation",
                             block_id="t1")

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec):
            rid = await ts._fire_one(_target(project, card))
            await _settle(60)
            return rid
    rid = asyncio.run(go())
    run = TaskRunStorage(get_project_dir(project.id)).get(rid)
    assert run.status == "held", run.status
    assert run.held_reason == "unsigned_escalation"
    assert run.launch_context == "headless"


def test_scheduled_run_emits_run_lifecycle_events(home, project):
    card = _card(project, _sched_block())
    events = []

    async def fake_push(run_id, event):
        events.append(event["type"])

    async def fake_exec(block, ctx):
        return Artifact(summary="ok")

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec), \
             patch("app.agents.run_driver._relay.safe_push", side_effect=fake_push):
            await ts._fire_one(_target(project, card))
            await _settle(60)
    asyncio.run(go())
    assert events == ["run_started", "run_completed"]


def test_startup_reconcile_leaves_a_live_scheduled_run_alone(home, project):
    """A sibling server starting mid-fire must not mark the run held."""
    card = _card(project, _sched_block())
    gate = asyncio.Event()
    seen = {}

    async def fake_exec(block, ctx):
        await gate.wait()
        return Artifact(summary="ok")

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec):
            rid = await ts._fire_one(_target(project, card))
            await _settle()
            # What app/server.py does at startup, from a FRESH storage.
            seen["reconciled"] = TaskRunStorage(get_project_dir(project.id)).reconcile_stale_runs()
            seen["status_mid"] = TaskRunStorage(get_project_dir(project.id)).get(rid).status
            gate.set()
            await _settle(60)
    asyncio.run(go())
    assert seen["reconciled"] == 0
    assert seen["status_mid"] == "running"


# -- fire_now ------------------------------------------------------------------

def test_fire_now_counts_as_a_fire_and_works_unregistered(home, project):
    card = _card(project, _sched_block(schedule_mode="daily_at", schedule_daily_at="02:00"))

    async def fake_exec(block, ctx):
        return Artifact(summary="ok")

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec):
            rid = await ts.fire_now(project.id, card.id)
            await _settle(60)
            return rid
    rid = asyncio.run(go())
    run = TaskRunStorage(get_project_dir(project.id)).get(rid)
    assert run.status == "done" and run.launch_context == "headless"
    assert run.card_snapshot["schedule_block_id"] == "s1"
    rec = ts._read_state(project.id)[card.id]
    assert rec["fires_so_far"] == 1 and rec["run_ids"] == [rid]
    with pytest.raises(ts.NotFireable):
        asyncio.run(ts.fire_now(project.id, "no-such-card"))


def test_fire_now_uses_the_registration_cadence_for_next_fire(home, project):
    card = _card(project, _sched_block(schedule_interval_value=1, schedule_interval_unit="hours"))
    reg.register(project.id, card, overrides={"schedule_interval_value": 3,
                                              "schedule_interval_unit": "days"})

    async def fake_exec(block, ctx):
        return Artifact(summary="ok")

    async def go():
        with patch("app.agents.block_executor.execute_block", side_effect=fake_exec):
            await ts.fire_now(project.id, card.id)
            await _settle(60)
    before = int(time.time() * 1000)
    asyncio.run(go())
    nxt = ts._read_state(project.id)[card.id]["next_fire_at"]
    assert nxt - before > 2.9 * 86_400_000


# -- cadence: defaults, timezone, validation -----------------------------------

def test_defaults_match_what_the_editor_shows():
    """Unset unit -> hours and unset daily time -> 09:00, as the editor
    displays; they used to be minutes / 00:00 so an untouched field fired
    on a cadence nobody saw."""
    now = int(time.time() * 1000)
    b = Block(block_type="schedule", id="s", name="s", body=[],
              schedule_mode="interval", schedule_interval_value=2)
    assert ts.compute_next_fire(b, now) - now == 2 * 3_600_000
    d = Block(block_type="schedule", id="s", name="s", body=[], schedule_mode="daily_at")
    fire = datetime.fromtimestamp(ts.compute_next_fire(d, now) / 1000)
    assert (fire.hour, fire.minute) == (9, 0)
    assert reg.cadence_from_block(b)["schedule_interval_unit"] == "hours"
    assert reg.cadence_from_block(d)["schedule_daily_at"] == "09:00"


def test_schedule_timezone_is_honoured():
    now = int(time.time() * 1000)
    tokyo = Block(block_type="schedule", id="s", name="s", body=[], schedule_mode="daily_at",
                  schedule_daily_at="09:00", schedule_timezone="Asia/Tokyo")
    utc = Block(block_type="schedule", id="s", name="s", body=[], schedule_mode="daily_at",
                schedule_daily_at="09:00", schedule_timezone="UTC")
    a, b = ts.compute_next_fire(tokyo, now), ts.compute_next_fire(utc, now)
    assert (b - a) % 86_400_000 == 9 * 3_600_000
    # A naive one-shot is read in the block's zone.
    at = Block(block_type="schedule", id="s", name="s", body=[], schedule_mode="at",
               schedule_at_iso="2099-01-01T09:00", schedule_timezone="UTC")
    from datetime import timezone
    assert ts.compute_next_fire(at, now) == int(
        datetime(2099, 1, 1, 9, tzinfo=timezone.utc).timestamp() * 1000)
    # An unknown zone falls back to local rather than silencing the card.
    bad = Block(block_type="schedule", id="s", name="s", body=[], schedule_mode="daily_at",
                schedule_daily_at="09:00", schedule_timezone="Mars/Olympus")
    assert ts.compute_next_fire(bad, now) is not None
    assert ts.cadence_problem(bad) is None


def test_cadence_problem_names_each_unreadable_shape():
    mk = lambda **kw: Block(block_type="schedule", id="s", name="s", body=[], **kw)
    assert ts.cadence_problem(mk(schedule_mode="interval", schedule_interval_value=1)) is None
    assert "at least 1" in ts.cadence_problem(mk(schedule_mode="interval", schedule_interval_value=0))
    assert "HH:MM" in ts.cadence_problem(mk(schedule_mode="daily_at", schedule_daily_at="25:00"))
    assert "5 fields" in ts.cadence_problem(mk(schedule_mode="cron", schedule_cron="0 3 * *"))
    assert "invalid" in ts.cadence_problem(mk(schedule_mode="cron", schedule_cron="99 3 * * *"))
    assert ts.cadence_problem(mk(schedule_mode="cron", schedule_cron="*/15 * * * *")) is None
    assert "ISO" in ts.cadence_problem(mk(schedule_mode="at", schedule_at_iso="tomorrow"))
    past = mk(schedule_mode="at", schedule_at_iso="2001-01-01T00:00")
    assert ts.cadence_problem(past) is None                       # fine to save
    assert "already passed" in ts.cadence_problem(past, int(time.time() * 1000))


def test_registry_refuses_an_unreadable_cadence(home, project):
    card = _card(project, _sched_block())
    with pytest.raises(ValueError, match="HH:MM"):
        reg.register(project.id, card, overrides={"schedule_mode": "daily_at",
                                                  "schedule_daily_at": "25:00"})
    assert reg.get_registration(project.id, card.id) is None
    reg.register(project.id, card)
    with pytest.raises(ValueError, match="5 fields"):
        reg.update_registration(project.id, card.id, {"schedule_mode": "cron",
                                                      "schedule_cron": "0 3 * *"})
    assert reg.get_registration(project.id, card.id).schedule_mode == "interval"
    with pytest.raises(ValueError, match="already passed"):
        reg.update_registration(project.id, card.id, {"schedule_mode": "at",
                                                      "schedule_at_iso": "2001-01-01T00:00"})


def test_api_returns_422_for_an_unreadable_cadence(home, project):
    from app.api import schedules as api
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    card = _card(project, _sched_block())
    r = client.post(f"/api/v1/projects/{project.id}/schedules/{card.id}",
                    json={"cadence": {"schedule_mode": "cron", "schedule_cron": "bogus"}})
    assert r.status_code == 422
    assert "5 fields" in r.json()["detail"]


def test_tick_records_why_a_registered_cadence_never_fires(home, project):
    """A registration written before validation existed (or a cron whose
    package vanished) must say what is wrong, not 'next slot not computed
    yet' for ever."""
    card = _card(project, _sched_block())
    bad = card.root.model_copy(update={"schedule_mode": "cron", "schedule_cron": "0 3 * *"})
    target = ts._ScheduledCard(project_id=project.id, card_id=card.id, card_name=card.name,
                               block=bad, body_root=ts._resolve_body_root(card.root))
    now = int(time.time() * 1000)
    asyncio.run(ts._tick_card(target, now))
    rec = ts._read_state(project.id)[card.id]
    assert rec["next_fire_at"] is None
    assert "5 fields" in rec["cadence_error"]
    # A corrected cadence clears it.
    good = ts._ScheduledCard(project_id=project.id, card_id=card.id, card_name=card.name,
                             block=card.root, body_root=ts._resolve_body_root(card.root))
    asyncio.run(ts._tick_card(good, now))
    rec = ts._read_state(project.id)[card.id]
    assert rec["next_fire_at"] is not None and "cadence_error" not in rec


def test_registration_row_reports_problem_and_finished_one_shot(home, project):
    card = _card(project, _sched_block())
    r = reg.register(project.id, card)
    row = reg.registration_row(project.id, r, card, {})
    assert row["problem"] is None
    # Bypass validation to simulate a legacy record with a bad cadence.
    regs = reg.read_registry(project.id)
    regs[card.id] = regs[card.id].model_copy(update={"schedule_mode": "cron", "schedule_cron": "x"})
    reg.write_registry(project.id, regs)
    row = reg.registration_row(project.id, regs[card.id], card, {})
    assert "5 fields" in row["problem"]
    # A fired one-shot is finished, not broken.
    once = regs[card.id].model_copy(update={"schedule_mode": "at",
                                            "schedule_at_iso": "2001-01-01T00:00"})
    row = reg.registration_row(project.id, once, card, {"fires_so_far": 1})
    assert row["exhausted"] is True and row["problem"] is None


def test_validator_refuses_bad_cadence_and_warns_on_nesting_and_past_at():
    from app.utils.task_card_validation import validate_card_tree
    bad = _sched_block(schedule_mode="cron", schedule_cron="0 3 * *")
    res = validate_card_tree(bad)
    assert not res.ok and any("5 fields" in f.message for f in res.errors)
    past = _sched_block(schedule_mode="at", schedule_at_iso="2001-01-01T00:00")
    res = validate_card_tree(past)
    assert res.ok and any("never fire if registered" in f.message for f in res.warnings)
    nested = Block(block_type="group", id="g", name="g", body=[_sched_block()])
    res = validate_card_tree(nested)
    assert res.ok and any("topmost" in f.message for f in res.warnings)
    assert validate_card_tree(_sched_block()).ok
