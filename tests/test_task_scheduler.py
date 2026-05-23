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
