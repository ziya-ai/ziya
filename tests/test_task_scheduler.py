"""Tests for the task-card scheduler.

Covers the time-math (compute_next_fire) and the lock semantics
independently — the fire loop itself is exercised via integration
by patching _dispatch_fire to a counter.

These tests deliberately do not start the real loop or make any
model calls; they verify the scheduler's deterministic logic.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.models.task_card import Block
from app.agents import task_scheduler as ts


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point ZIYA_HOME at a fresh temp dir so lock and state files
    don't collide with the real environment."""
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return tmp_path


# ── compute_next_fire ───────────────────────────────────────

def test_compute_next_fire_interval_minutes():
    block = Block(
        block_type="schedule", id="s1", name="every 15m",
        schedule_mode="interval",
        schedule_interval_value=15, schedule_interval_unit="minutes",
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    expected = _ms(datetime(2026, 1, 1, 12, 15, 0))
    assert ts.compute_next_fire(block, base) == expected


def test_compute_next_fire_interval_hours():
    block = Block(
        block_type="schedule", id="s1", name="every 2h",
        schedule_mode="interval",
        schedule_interval_value=2, schedule_interval_unit="hours",
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    expected = _ms(datetime(2026, 1, 1, 14, 0, 0))
    assert ts.compute_next_fire(block, base) == expected


def test_compute_next_fire_at_future():
    target = datetime(2099, 1, 1, 12, 0, 0)
    block = Block(
        block_type="schedule", id="s1", name="at",
        schedule_mode="at", schedule_at_iso=target.isoformat(),
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    assert ts.compute_next_fire(block, base) == _ms(target)


def test_compute_next_fire_at_past_returns_none():
    """One-shot 'at' that's already past must not re-fire."""
    target = datetime(2000, 1, 1, 0, 0, 0)
    block = Block(
        block_type="schedule", id="s1", name="at",
        schedule_mode="at", schedule_at_iso=target.isoformat(),
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    assert ts.compute_next_fire(block, base) is None


def test_compute_next_fire_daily_at_today_future():
    """If the daily time is later today, that's the next fire."""
    block = Block(
        block_type="schedule", id="s1", name="daily",
        schedule_mode="daily_at", schedule_daily_at="18:30",
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    expected = _ms(datetime(2026, 1, 1, 18, 30, 0))
    assert ts.compute_next_fire(block, base) == expected


def test_compute_next_fire_daily_at_today_past_rolls_over():
    """If the daily time has already passed today, fire tomorrow."""
    block = Block(
        block_type="schedule", id="s1", name="daily",
        schedule_mode="daily_at", schedule_daily_at="06:00",
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    expected = _ms(datetime(2026, 1, 2, 6, 0, 0))
    assert ts.compute_next_fire(block, base) == expected


def test_compute_next_fire_cron():
    """Cron requires croniter; if not installed the call returns None."""
    block = Block(
        block_type="schedule", id="s1", name="cron",
        schedule_mode="cron", schedule_cron="*/15 * * * *",
    )
    base = _ms(datetime(2026, 1, 1, 12, 7, 0))
    result = ts.compute_next_fire(block, base)
    try:
        import croniter  # noqa: F401
    except ImportError:
        assert result is None
    else:
        # 12:07 → next */15 fire is 12:15
        expected = _ms(datetime(2026, 1, 1, 12, 15, 0))
        assert result == expected


def test_compute_next_fire_unparseable_at():
    block = Block(
        block_type="schedule", id="s1", name="bad",
        schedule_mode="at", schedule_at_iso="not a date",
    )
    base = _ms(datetime(2026, 1, 1, 12, 0, 0))
    assert ts.compute_next_fire(block, base) is None


# ── _find_schedule_block ────────────────────────────────────

def test_find_schedule_block_at_root():
    sched = Block(block_type="schedule", id="s", name="s", schedule_mode="interval")
    assert ts._find_schedule_block(sched) is sched


def test_find_schedule_block_nested():
    inner = Block(block_type="schedule", id="s", name="s", schedule_mode="interval")
    outer = Block(
        block_type="repeat", id="r", name="r",
        repeat_mode="count", repeat_count=1, body=[inner],
    )
    assert ts._find_schedule_block(outer) is inner


def test_find_schedule_block_absent():
    task = Block(block_type="task", id="t", name="t", instructions="hi")
    assert ts._find_schedule_block(task) is None


# ── lock semantics ──────────────────────────────────────────

def test_lock_acquired_when_absent(isolated_home):
    handle = ts._acquire_lock()
    assert handle is not None
    assert handle.is_mine()
    lock_path = isolated_home / "scheduler.lock"
    assert lock_path.exists()
    data = json.loads(lock_path.read_text())
    assert data["pid"] == os.getpid()


def test_lock_blocked_when_held_by_live_peer(isolated_home):
    """A fresh, recently-heartbeat-stamped lock from a different pid
    must block acquisition."""
    lock_path = isolated_home / "scheduler.lock"
    lock_path.write_text(json.dumps({
        "pid": os.getpid() + 1,  # different pid
        "host": "other-host",
        "heartbeat_ms": int(time.time() * 1000),
    }))
    handle = ts._acquire_lock()
    assert handle is None


def test_lock_takeover_when_stale(isolated_home):
    """A stale lock (heartbeat > threshold ago) must be takeable."""
    lock_path = isolated_home / "scheduler.lock"
    stale_ms = int((time.time() - ts._LOCK_STALE_THRESHOLD_S - 30) * 1000)
    lock_path.write_text(json.dumps({
        "pid": 999_999, "host": "dead-host", "heartbeat_ms": stale_ms,
    }))
    handle = ts._acquire_lock()
    assert handle is not None
    assert handle.is_mine()


# ── start/cancel idempotency ────────────────────────────────

@pytest.mark.asyncio
async def test_start_scheduler_acquires_lock_then_cancel_clean(isolated_home):
    """End-to-end against the real start_scheduler() API:

      1. Spawn the loop.
      2. Yield long enough for it to acquire the lock and write the
         lock file.
      3. Cancel the task.  It must exit promptly via CancelledError
         and remove the lock file so a peer process could take over.

    The loop's tick body is replaced with a stub that no-ops so the
    test doesn't depend on real card enumeration or model calls.
    """
    async def _noop_pass(*_args, **_kwargs):
        return None

    # Replace the per-tick fire pass with a no-op to keep the test
    # deterministic and free of model/storage I/O.
    with patch.object(ts, "_enumerate_scheduled_cards", return_value=[]), \
         patch.object(ts, "_TICK_INTERVAL_S", 0.05):
        task = ts.start_scheduler()
        try:
            # Give the loop a chance to acquire the lock.
            for _ in range(40):
                if (isolated_home / "scheduler.lock").exists():
                    break
                await asyncio.sleep(0.05)
            assert (isolated_home / "scheduler.lock").exists()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    # On clean shutdown the loop releases its lock so a peer could
    # take over without waiting for the staleness threshold.
    assert not (isolated_home / "scheduler.lock").exists()


@pytest.mark.asyncio
async def test_start_scheduler_returns_task(isolated_home):
    """start_scheduler returns an asyncio.Task that callers can cancel."""
    with patch.object(ts, "_enumerate_scheduled_cards", return_value=[]), \
         patch.object(ts, "_TICK_INTERVAL_S", 0.05):
        task = ts.start_scheduler()
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ── state file round-trip ───────────────────────────────────

def test_state_round_trip(isolated_home, tmp_path, monkeypatch):
    """_write_state then _read_state preserves the dict."""
    # The state path is rooted at get_project_dir(project_id), which
    # in turn uses ZIYA_HOME (already set by isolated_home).
    pid = "proj-test"
    (isolated_home / "projects" / pid).mkdir(parents=True, exist_ok=True)
    state = {
        "card-1": {"block_id": "s1", "fires_so_far": 3,
                   "last_fire_at": 1700000000000,
                   "next_fire_at": 1700003600000,
                   "run_ids": ["r1", "r2", "r3"]},
    }
    ts._write_state(pid, state)
    out = ts._read_state(pid)
    assert out == state


def test_read_state_missing_file_returns_empty(isolated_home):
    """No state file → empty dict (not an error)."""
    assert ts._read_state("never-written") == {}


def test_read_state_corrupt_json_returns_empty(isolated_home):
    """Garbage in the state file is logged and recovered as empty."""
    pid = "proj-corrupt"
    p = isolated_home / "projects" / pid
    p.mkdir(parents=True, exist_ok=True)
    (p / "schedule_state.json").write_text("{ not json")
    assert ts._read_state(pid) == {}


# ── _resolve_body_root ──────────────────────────────────────

def test_resolve_body_root_single_child_returns_child():
    child = Block(block_type="task", id="t", name="t", instructions="hi")
    sched = Block(
        block_type="schedule", id="s", name="s",
        schedule_mode="interval", body=[child],
    )
    assert ts._resolve_body_root(sched) is child


def test_resolve_body_root_multi_child_wraps_in_repeat_count_one():
    """Multi-child schedule body wraps into a Repeat-count-1 to
    preserve sequence semantics during a fire."""
    a = Block(block_type="task", id="a", name="a")
    b = Block(block_type="task", id="b", name="b")
    sched = Block(
        block_type="schedule", id="s", name="s",
        schedule_mode="interval", body=[a, b],
    )
    wrapped = ts._resolve_body_root(sched)
    assert wrapped.block_type == "repeat"
    assert wrapped.repeat_mode == "count"
    assert wrapped.repeat_count == 1
    assert wrapped.body == [a, b]


def test_resolve_body_root_empty_returns_self():
    """Empty body returns the schedule itself; passthrough handles it."""
    sched = Block(
        block_type="schedule", id="s", name="s",
        schedule_mode="interval", body=[],
    )
    assert ts._resolve_body_root(sched) is sched


# ── _enumerate_scheduled_cards ──────────────────────────────

def _make_project_with_card(home, pid: str, card_dict: dict) -> None:
    """Helper: write the bare minimum project + task-card files
    that the storage layer needs to enumerate them."""
    import json
    proj_dir = home / "projects" / pid
    (proj_dir / "task_cards").mkdir(parents=True, exist_ok=True)
    # Project record (matches Project model loosely; storage only needs id)
    (home / "projects").mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": str(home / "src" / pid),
        "createdAt": 0, "lastAccessedAt": 0,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
    }))
    # The task card itself
    cid = card_dict["id"]
    (proj_dir / "task_cards" / f"{cid}.json").write_text(json.dumps(card_dict))


def test_enumerate_skips_cards_without_schedule(isolated_home):
    """A card whose root has no schedule block must not appear."""
    _make_project_with_card(isolated_home, "p1", {
        "id": "c-no-sched", "name": "no-sched", "description": "",
        "tags": [], "is_template": False, "source": "custom",
        "created_at": 0, "updated_at": 0, "run_count": 0,
        "root": {
            "block_type": "task", "id": "t", "name": "t",
            "instructions": "x", "body": [],
        },
    })
    assert ts._enumerate_scheduled_cards() == []


def test_enumerate_skips_disabled_schedules(isolated_home):
    """schedule_enabled=False → not enumerated."""
    _make_project_with_card(isolated_home, "p1", {
        "id": "c-off", "name": "off", "description": "",
        "tags": [], "is_template": False, "source": "custom",
        "created_at": 0, "updated_at": 0, "run_count": 0,
        "root": {
            "block_type": "schedule", "id": "s", "name": "s",
            "schedule_mode": "interval",
            "schedule_interval_value": 1,
            "schedule_interval_unit": "hours",
            "schedule_enabled": False,
            "body": [{"block_type": "task", "id": "t", "name": "t",
                      "instructions": "x", "body": []}],
        },
    })
    assert ts._enumerate_scheduled_cards() == []


def test_enumerate_finds_enabled_schedule(isolated_home):
    """An enabled schedule appears with the right project/card/block ids."""
    _make_project_with_card(isolated_home, "p1", {
        "id": "c-on", "name": "on", "description": "",
        "tags": [], "is_template": False, "source": "custom",
        "created_at": 0, "updated_at": 0, "run_count": 0,
        "root": {
            "block_type": "schedule", "id": "s-on", "name": "s",
            "schedule_mode": "interval",
            "schedule_interval_value": 1,
            "schedule_interval_unit": "hours",
            "schedule_enabled": True,
            "body": [{"block_type": "task", "id": "t", "name": "t",
                      "instructions": "x", "body": []}],
        },
    })
    found = ts._enumerate_scheduled_cards()
    assert len(found) == 1
    assert found[0].project_id == "p1"
    assert found[0].card_id == "c-on"
    assert found[0].block.id == "s-on"


def test_enumerate_finds_nested_schedule(isolated_home):
    """A schedule nested inside a Repeat is still discovered (the
    walker descends — design guarantee for 'outer-outer' nesting)."""
    _make_project_with_card(isolated_home, "p1", {
        "id": "c-nested", "name": "nested", "description": "",
        "tags": [], "is_template": False, "source": "custom",
        "created_at": 0, "updated_at": 0, "run_count": 0,
        "root": {
            "block_type": "repeat", "id": "r", "name": "r",
            "repeat_mode": "count", "repeat_count": 1,
            "body": [{
                "block_type": "schedule", "id": "s-inner", "name": "s",
                "schedule_mode": "interval",
                "schedule_interval_value": 1,
                "schedule_interval_unit": "hours",
                "schedule_enabled": True,
                "body": [{"block_type": "task", "id": "t", "name": "t",
                          "instructions": "x", "body": []}],
            }],
        },
    })
    found = ts._enumerate_scheduled_cards()
    assert len(found) == 1
    assert found[0].block.id == "s-inner"


# ── _fire_one: launch-time audit snapshots ──────────────────

def test_fire_one_records_card_and_permissions_snapshot(isolated_home):
    """A scheduled fire must record the same launch-time snapshots the
    interactive launch path does.

    The scheduler re-reads the card every tick, so a fire runs the CURRENT
    definition; without ``card_snapshot`` nothing on the run says which
    definition that was, and resume-from-block (which walks
    ``card_snapshot.root``) cannot work on scheduled runs at all.  The
    snapshot must describe the resolved body — the tree ``block_states``
    were seeded from — not the raw card root with the schedule wrapper.
    """
    from app.models.project import ProjectCreate
    from app.models.task_card import Artifact, TaskCardCreate
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    repo = isolated_home / "repo"
    repo.mkdir()
    proj = ProjectStorage(get_ziya_home()).create(
        ProjectCreate(path=str(repo), name="repo"))
    body = Block(block_type="task", id="t1", name="t1", instructions="x")
    # A scope on the SCHEDULE block: _go() seeds it onto the executor's
    # scope stack even though the block is not in body_root, so the
    # permissions snapshot must show it reaching the body's tasks.
    from app.models.task_card import TaskScope
    sched = Block(block_type="schedule", id="s1", name="nightly",
                  schedule_mode="interval", schedule_interval_value=1,
                  schedule_interval_unit="hours", body=[body],
                  scope=TaskScope(tools=["file_read"]))
    card = TaskCardStorage(get_project_dir(proj.id)).create(
        TaskCardCreate(name="sched", description="d", root=sched))
    target = ts._ScheduledCard(
        project_id=proj.id, card_id=card.id, card_name="sched",
        block=card.root, body_root=ts._resolve_body_root(card.root))

    async def fake_exec(block, ctx):
        return Artifact(summary="ok")

    async def go():
        with patch("app.agents.block_executor.execute_block",
                   side_effect=fake_exec):
            await ts._fire_one(target)
            for _ in range(20):
                await asyncio.sleep(0)
    asyncio.run(go())

    run_storage = TaskRunStorage(get_project_dir(proj.id))
    runs = run_storage.list()
    assert len(runs) == 1
    run = run_storage.get(runs[0].id)

    snap = run.card_snapshot
    assert snap is not None, "scheduled fire recorded no card_snapshot"
    assert snap["name"] == "sched"
    assert snap["version"] == getattr(card, "version", None)
    assert snap["schedule_block_id"] == "s1"
    # The snapshot tree is the resolved body: its ids are exactly the
    # ids block_states were seeded with, so resume can map one to the
    # other.  The schedule wrapper itself is not part of the run's tree.
    assert snap["root"]["id"] == "t1"
    assert set(run.block_states or {}) == {"t1"}

    assert run.permissions_snapshot is not None, \
        "scheduled fire recorded no permissions_snapshot"
    scopes = run.permissions_snapshot.get("block_scopes") or {}
    assert "t1" in scopes, scopes
    assert "file_read" in scopes["t1"]["tools"]


# ── _tick_card: schedule_concurrency ────────────────────────

def _scheduled_target(isolated_home, concurrency=None, catch_up=True):
    """A real project + card with a one-task Schedule body, returned as the
    _ScheduledCard the loop would build, plus the storages the test needs."""
    from app.models.project import ProjectCreate
    from app.models.task_card import TaskCardCreate
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.storage.task_runs import TaskRunStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    repo = isolated_home / "repo"
    repo.mkdir(exist_ok=True)
    proj = ProjectStorage(get_ziya_home()).create(
        ProjectCreate(path=str(repo), name="repo"))
    body = Block(block_type="task", id="t1", name="t1", instructions="x")
    kw = {} if concurrency is None else {"schedule_concurrency": concurrency}
    sched = Block(block_type="schedule", id="s1", name="nightly",
                  schedule_mode="interval", schedule_interval_value=1,
                  schedule_interval_unit="hours", schedule_catch_up=catch_up,
                  body=[body], **kw)
    card = TaskCardStorage(get_project_dir(proj.id)).create(
        TaskCardCreate(name="sched", description="d", root=sched))
    target = ts._ScheduledCard(
        project_id=proj.id, card_id=card.id, card_name="sched",
        block=card.root, body_root=ts._resolve_body_root(card.root))
    return target, TaskRunStorage(get_project_dir(proj.id))


def _hand_launch_live_run(run_storage, card_id, status="running"):
    """A run of the card started by some other path (interactive launch),
    left in a live status — exactly what a 03:00 catch-up fire at 09:00
    collides with."""
    from app.models.task_run import TaskRunCreate
    run = run_storage.create(TaskRunCreate(card_id=card_id, source_conversation_id=None))
    run_storage.update_status(run.id, status)
    return run


def _due_state(target, now_ms, fires_so_far=1):
    ts._write_state(target.project_id, {target.card_id: {
        "block_id": "s1", "fires_so_far": fires_so_far,
        "last_fire_at": now_ms - 3_600_000, "next_fire_at": now_ms - 60_000,
        "run_ids": [],
    }})


def _tick(target, now_ms):
    fired = []

    async def fake_fire(t):
        fired.append(t.card_id)

    async def go():
        with patch.object(ts, "_fire_one", side_effect=fake_fire):
            await ts._tick_card(target, now_ms)
    asyncio.run(go())
    return fired


def test_tick_fires_when_due_and_no_live_run(isolated_home):
    """Positive control: the gate lets a due slot through when nothing is
    live, so the skip assertions below cannot pass by never firing."""
    target, _ = _scheduled_target(isolated_home)
    now = int(time.time() * 1000)
    _due_state(target, now)
    assert _tick(target, now) == [target.card_id]


def test_tick_default_skips_when_a_run_of_the_card_is_live(isolated_home):
    """Unset schedule_concurrency means skip: a due slot is dropped while a
    hand-launched run is live, the skip is on disk (so the schedule-state
    endpoint shows it) and the slot advances so it is not retried every tick."""
    target, runs = _scheduled_target(isolated_home)
    live = _hand_launch_live_run(runs, target.card_id)
    now = int(time.time() * 1000)
    _due_state(target, now)

    assert _tick(target, now) == []

    rec = ts._read_state(target.project_id)[target.card_id]
    assert rec["skips_so_far"] == 1
    assert rec["last_skip_at"] == now
    assert rec["skips"][0]["live_run_id"] == live.id
    assert live.id[:8] in rec["skips"][0]["reason"]
    assert "still live" in rec["skips"][0]["reason"]
    assert rec["next_fire_at"] > now, "skipped slot must advance, not re-arm"
    assert rec["fires_so_far"] == 1, "a skip is not a fire"
    # Once the live run ends the next slot fires normally.
    runs.update_status(live.id, "done")
    _due_state(target, now)
    assert _tick(target, now) == [target.card_id]


def test_tick_skip_applies_to_catch_up_fires(isolated_home):
    """The laptop-wakes-at-09:00 case: a missed 03:00 slot (catch-up) is
    gated like any other slot.  The gate runs before the catch-up logic."""
    target, runs = _scheduled_target(isolated_home, catch_up=True)
    _hand_launch_live_run(runs, target.card_id)
    now = int(time.time() * 1000)
    # Six hours overdue.
    ts._write_state(target.project_id, {target.card_id: {
        "block_id": "s1", "fires_so_far": 3,
        "last_fire_at": now - 30 * 3_600_000, "next_fire_at": now - 6 * 3_600_000,
        "run_ids": [],
    }})
    assert _tick(target, now) == []
    rec = ts._read_state(target.project_id)[target.card_id]
    assert rec["skips_so_far"] == 1


def test_tick_queue_defers_then_fires_when_live_run_ends(isolated_home):
    target, runs = _scheduled_target(isolated_home, concurrency="queue")
    live = _hand_launch_live_run(runs, target.card_id)
    now = int(time.time() * 1000)
    _due_state(target, now)

    assert _tick(target, now) == []
    rec = ts._read_state(target.project_id)[target.card_id]
    assert rec["deferred_since"] == now
    assert rec["deferred_behind_run_id"] == live.id
    assert rec["next_fire_at"] == now - 60_000, "queue holds the slot"
    assert "skips_so_far" not in rec

    # Still live on the next tick: still held, deferred_since unchanged.
    assert _tick(target, now + 15_000) == []
    assert ts._read_state(target.project_id)[target.card_id]["deferred_since"] == now

    runs.update_status(live.id, "done")
    assert _tick(target, now + 30_000) == [target.card_id]
    rec = ts._read_state(target.project_id)[target.card_id]
    assert "deferred_since" not in rec and "deferred_behind_run_id" not in rec


def test_tick_queue_deferral_bypasses_catch_up_gate(isolated_home):
    """A deferred slot is a decision to run late, not a missed slot: with
    catch_up=False it still fires once the live run ends."""
    target, runs = _scheduled_target(isolated_home, concurrency="queue", catch_up=False)
    live = _hand_launch_live_run(runs, target.card_id)
    now = int(time.time() * 1000)
    _due_state(target, now)
    assert _tick(target, now) == []
    runs.update_status(live.id, "cancelled")
    assert _tick(target, now + 15_000) == [target.card_id]


def test_tick_parallel_fires_despite_live_run(isolated_home):
    target, runs = _scheduled_target(isolated_home, concurrency="parallel")
    _hand_launch_live_run(runs, target.card_id)
    now = int(time.time() * 1000)
    _due_state(target, now)
    assert _tick(target, now) == [target.card_id]
    assert "skips_so_far" not in ts._read_state(target.project_id)[target.card_id]


def test_tick_terminal_runs_do_not_block(isolated_home):
    """Only LIVE statuses gate; a failed or held predecessor is not an overlap."""
    target, runs = _scheduled_target(isolated_home)
    for st in ("done", "failed", "held", "partial", "cancelled"):
        _hand_launch_live_run(runs, target.card_id, status=st)
    now = int(time.time() * 1000)
    _due_state(target, now)
    assert _tick(target, now) == [target.card_id]
