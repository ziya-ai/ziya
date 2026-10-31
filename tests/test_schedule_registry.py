"""
Schedule registrations (app/agents/schedule_registry.py) and the seams
they cross:

  * the scheduler enumerates REGISTRATIONS, not cards: a card with an
    enabled Schedule block fires nothing until registered; a registered
    draft (conversation-only) card does fire; a registered card without a
    Schedule block runs its root
  * register / update / unregister, and a cadence edit resetting the fire
    record's next_fire_at
  * migration of pre-registration schedule_state.json records, once
  * the /schedules API and the scheduler-status endpoint
  * /schedule-state says whether the card is registered
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents import schedule_registry as reg
from app.agents import task_scheduler as ts
from app.models.project import ProjectCreate
from app.models.task_card import Block, TaskCardCreate
from app.storage.projects import ProjectStorage
from app.storage.task_cards import TaskCardStorage
from app.utils.paths import get_project_dir, get_ziya_home


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def project(home):
    repo = home / "repo"
    repo.mkdir()
    return ProjectStorage(get_ziya_home()).create(ProjectCreate(path=str(repo), name="repo"))


def _sched_block(bid="s1", enabled=True, **cadence) -> Block:
    cadence.setdefault("schedule_mode", "interval")
    cadence.setdefault("schedule_interval_value", 1)
    cadence.setdefault("schedule_interval_unit", "hours")
    return Block(block_type="schedule", id=bid, name="nightly", schedule_enabled=enabled,
                 body=[Block(block_type="task", id=f"{bid}-t", name="t", instructions="x")],
                 **cadence)


def _card(project, root: Block, name="c", draft=False):
    return TaskCardStorage(get_project_dir(project.id)).create(
        TaskCardCreate(name=name, description="", root=root, draft=draft))


# -- the seam: scheduler reads registrations ----------------------------------

def test_card_with_schedule_block_is_not_enumerated_until_registered(home, project):
    card = _card(project, _sched_block())
    assert ts._enumerate_scheduled_cards() == []
    reg.register(project.id, card)
    found = ts._enumerate_scheduled_cards()
    assert [t.card_id for t in found] == [card.id]
    assert found[0].block.id == "s1"                 # source block's id and scope travel
    assert found[0].block.schedule_interval_unit == "hours"
    assert found[0].body_root.id == "s1-t"           # the body, not the trigger


def test_registered_draft_card_is_enumerated(home, project):
    """The draft gap: conversation-only cards never fired because the
    scheduler listed deck cards.  Registration is what is durable now."""
    card = _card(project, _sched_block(), draft=True)
    assert TaskCardStorage(get_project_dir(project.id)).list() == []   # hidden from the deck
    reg.register(project.id, card)
    assert [t.card_id for t in ts._enumerate_scheduled_cards()] == [card.id]


def test_registration_cadence_wins_over_the_block(home, project):
    card = _card(project, _sched_block(schedule_mode="daily_at", schedule_daily_at="02:00"))
    reg.register(project.id, card, overrides={"schedule_daily_at": "04:30"})
    found = ts._enumerate_scheduled_cards()
    assert found[0].block.schedule_daily_at == "04:30"


def test_disabled_registration_is_not_enumerated_and_block_flag_is_ignored(home, project):
    card = _card(project, _sched_block(enabled=False))         # author's flag: irrelevant
    reg.register(project.id, card)
    assert len(ts._enumerate_scheduled_cards()) == 1
    reg.update_registration(project.id, card.id, {"enabled": False})
    assert ts._enumerate_scheduled_cards() == []


def test_card_without_schedule_block_runs_its_root(home, project):
    root = Block(block_type="task", id="plain", name="p", instructions="x")
    card = _card(project, root)
    r = reg.register(project.id, card)
    assert r.source_block_id is None and r.schedule_mode == "daily_at"
    found = ts._enumerate_scheduled_cards()
    assert found[0].body_root.id == "plain"
    assert found[0].block.id == f"registration:{card.id}"


def test_registration_for_missing_card_is_skipped(home, project):
    card = _card(project, _sched_block())
    reg.register(project.id, card)
    TaskCardStorage(get_project_dir(project.id)).delete(card.id)
    assert ts._enumerate_scheduled_cards() == []
    rows = reg.list_schedules(project.id)["registrations"]
    assert rows[0]["card_missing"] is True


# -- register / update / unregister --------------------------------------------

def test_register_twice_is_refused(home, project):
    card = _card(project, _sched_block())
    reg.register(project.id, card)
    with pytest.raises(reg.AlreadyRegistered):
        reg.register(project.id, card)


def test_cadence_edit_resets_next_fire_but_enable_flip_does_not(home, project):
    card = _card(project, _sched_block())
    reg.register(project.id, card)
    far = 4_000_000_000_000
    ts._write_state(project.id, {card.id: {"next_fire_at": far, "fires_so_far": 3,
                                           "deferred_since": 1, "run_ids": []}})
    reg.update_registration(project.id, card.id, {"enabled": False})
    assert ts._read_state(project.id)[card.id]["next_fire_at"] == far
    reg.update_registration(project.id, card.id, {"schedule_interval_unit": "minutes"})
    rec = ts._read_state(project.id)[card.id]
    assert rec["next_fire_at"] < far
    assert "deferred_since" not in rec
    assert rec["fires_so_far"] == 3                   # history kept


def test_unregister_removes_registration_and_fire_record(home, project):
    card = _card(project, _sched_block())
    reg.register(project.id, card)
    ts._write_state(project.id, {card.id: {"fires_so_far": 9, "run_ids": []}})
    assert reg.unregister(project.id, card.id) is True
    assert reg.unregister(project.id, card.id) is False
    assert reg.get_registration(project.id, card.id) is None
    assert card.id not in ts._read_state(project.id)
    # Re-registering starts the max_runs count over.
    reg.register(project.id, card, overrides={"schedule_max_runs": 2})
    assert ts._read_state(project.id).get(card.id) is None


def test_block_edit_after_registration_does_not_change_cadence(home, project):
    """The registration owns the cadence; the card is only the request."""
    storage = TaskCardStorage(get_project_dir(project.id))
    card = _card(project, _sched_block(schedule_interval_value=1))
    reg.register(project.id, card)
    edited = card.model_copy(deep=True)
    edited.root.schedule_interval_value = 7
    from app.models.task_card import TaskCardUpdate
    storage.update(card.id, TaskCardUpdate(root=edited.root))
    assert ts._enumerate_scheduled_cards()[0].block.schedule_interval_value == 1


# -- migration -----------------------------------------------------------------

def test_armed_state_records_migrate_once(home, project):
    storage = TaskCardStorage(get_project_dir(project.id))
    armed = _card(project, _sched_block("a", enabled=True), name="armed")
    paused = _card(project, _sched_block("p", enabled=False), name="paused")
    never = _card(project, _sched_block("n"), name="never-armed")
    plain = _card(project, Block(block_type="task", id="x", name="x", instructions="x"), name="plain")
    ts._write_state(project.id, {
        armed.id: {"block_id": "a", "fires_so_far": 2, "last_fire_at": 1_700_000_000_000, "run_ids": []},
        paused.id: {"block_id": "p", "fires_so_far": 0, "next_fire_at": 1, "run_ids": []},
        plain.id: {"fires_so_far": 1, "run_ids": []},            # card lost its schedule block
        "gone": {"fires_so_far": 1, "run_ids": []},              # card deleted
    })
    regs = reg.ensure_migrated(project.id, storage)
    assert set(regs) == {armed.id, paused.id}
    assert regs[armed.id].migrated and regs[armed.id].registered_at == 1_700_000_000_000
    assert regs[armed.id].enabled is True and regs[paused.id].enabled is False
    assert never.id not in regs
    # Second call reads the file; a new state record does not re-migrate.
    ts._write_state(project.id, {never.id: {"fires_so_far": 1, "run_ids": []}})
    assert set(reg.ensure_migrated(project.id, storage)) == {armed.id, paused.id}
    listing = reg.list_schedules(project.id)
    assert {u["card_id"] for u in listing["unregistered"]} == {never.id}


# -- API -----------------------------------------------------------------------

@pytest.fixture
def client(home):
    from app.api import schedules as api
    from app.api import task_cards as tc_api
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(api.status_router)
    app.include_router(tc_api.router)
    return TestClient(app)


def test_api_register_list_patch_unregister(home, project, client):
    card = _card(project, _sched_block(schedule_mode="daily_at", schedule_daily_at="02:00"))
    base = f"/api/v1/projects/{project.id}/schedules"

    r = client.get(base)
    assert r.status_code == 200
    assert r.json()["registrations"] == []
    assert [u["card_id"] for u in r.json()["unregistered"]] == [card.id]
    assert r.json()["unregistered"][0]["requested"]["schedule_daily_at"] == "02:00"
    assert "scheduler" in r.json()

    r = client.post(f"{base}/{card.id}", json={"cadence": {"schedule_daily_at": "03:15"}})
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["enabled"] is True and row["registered_at"] > 0
    assert row["summary"] == "daily at 03:15"
    assert client.post(f"{base}/{card.id}", json={}).status_code == 409
    assert client.post(f"{base}/no-such-card", json={}).status_code == 404

    r = client.patch(f"{base}/{card.id}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert r.json()["next_fire_at"] is None             # disabled: nothing projected
    r = client.patch(f"{base}/{card.id}", json={"cadence": {"schedule_mode": "interval",
                                                           "schedule_interval_value": 2,
                                                           "schedule_interval_unit": "hours"}})
    assert r.json()["summary"] == "every 2 hours"
    assert reg.get_registration(project.id, card.id).schedule_interval_value == 2

    r = client.get(base)
    assert [x["card_id"] for x in r.json()["registrations"]] == [card.id]
    assert r.json()["unregistered"] == []

    assert client.delete(f"{base}/{card.id}").status_code == 204
    assert client.delete(f"{base}/{card.id}").status_code == 404
    assert client.get(base).json()["registrations"] == []


def test_api_list_shows_exhausted_and_fire_history(home, project, client):
    card = _card(project, _sched_block())
    reg.register(project.id, card, overrides={"schedule_max_runs": 2})
    ts._write_state(project.id, {card.id: {"fires_so_far": 2, "last_fire_at": 5, "next_fire_at": 99,
                                           "skips_so_far": 1, "run_ids": []}})
    row = client.get(f"/api/v1/projects/{project.id}/schedules").json()["registrations"][0]
    assert row["exhausted"] is True and row["next_fire_at"] is None
    assert row["fires_so_far"] == 2 and row["skips_so_far"] == 1 and row["last_fire_at"] == 5


def test_api_scheduler_status_reports_lock_holder(home, client):
    r = client.get("/api/v1/scheduler/status")
    assert r.status_code == 200
    assert r.json()["held"] is False and r.json()["this_process"] is False
    handle = ts._acquire_lock()
    assert handle is not None
    s = client.get("/api/v1/scheduler/status").json()
    assert s["held"] is True and s["this_process"] is True and s["pid"] == os.getpid()
    assert s["heartbeat_age_s"] is not None
    handle.path.write_text(json.dumps({"pid": 1, "host": "elsewhere", "heartbeat_ms": 0}))
    s = client.get("/api/v1/scheduler/status").json()
    assert s["held"] is False and s["stale"] is True and s["pid"] == 1


def test_real_server_mounts_schedule_routes():
    """The tests above build their own FastAPI app, so they would pass with
    the router never included in app/server.py.  Check the real app."""
    from app.server import app as server_app
    paths = {getattr(r, "path", "") for r in server_app.routes}
    assert "/api/v1/projects/{project_id}/schedules" in paths
    assert "/api/v1/projects/{project_id}/schedules/{card_id}" in paths
    assert "/api/v1/scheduler/status" in paths


def test_schedule_state_endpoint_says_whether_registered(home, project, client):
    card = _card(project, _sched_block())
    url = f"/api/v1/projects/{project.id}/task-cards/{card.id}/schedule-state"
    r = client.get(url).json()
    assert r["registered"] is False and r.get("enabled") is None
    reg.register(project.id, card)
    reg.update_registration(project.id, card.id, {"enabled": False})
    r = client.get(url).json()
    assert r["registered"] is True and r["enabled"] is False
