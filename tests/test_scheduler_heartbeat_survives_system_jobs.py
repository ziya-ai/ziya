"""The scheduler loop must keep heartbeating while a system job runs.

Attribution (user report, 2026-10-09): two Ziya servers sharing one
~/.ziya both ran memory_organize at once and both stalled their users for
~10 minutes, even though the scheduler is documented as single-writer via
~/.ziya/scheduler.lock.  The lock holder heartbeats only from its own loop
body, and the loop body did ``await tick_system_jobs()`` INLINE -- so for the
whole of a multi-minute organize no heartbeat was written.  After 90 s the
peer judged the lock stale, took it over, and started its own organize.  The
two then fought over the memories filelock (``could not be acquired`` in the
dumps), each poll-sleep landing on its own event loop.  The loop blocking and
the lock loss are the same defect: the single-writer guarantee depended on a
loop that the job was parking.

Fix: the loop spawns the system-job pass as a task and does not await it, so
heartbeats continue; a pass still in flight is not re-spawned.  Both
assertions fail on the unpatched module (no ``_spawn_system_jobs_tick``, and
the loop awaits inline).
"""
import asyncio
import inspect

import pytest

from app.agents import task_scheduler as ts
import app.agents.system_jobs as sj


@pytest.mark.asyncio
async def test_system_job_pass_is_spawned_not_awaited_and_not_doubled(monkeypatch):
    started = 0
    release = asyncio.Event()

    async def slow_tick(now_ms=None):
        nonlocal started
        started += 1
        await release.wait()
        return ["memory_organize"]

    monkeypatch.setattr(sj, "tick_system_jobs", slow_tick)
    ts._system_jobs_task = None

    t0 = asyncio.get_running_loop().time()
    first = ts._spawn_system_jobs_tick(1)
    second = ts._spawn_system_jobs_tick(2)        # pass still in flight: skipped
    await asyncio.sleep(0)
    assert asyncio.get_running_loop().time() - t0 < 0.5   # returned without waiting
    assert first is True and second is False
    assert started == 1

    release.set()
    await ts._system_jobs_task
    assert ts._spawn_system_jobs_tick(3) is True   # a finished pass can be followed
    await asyncio.sleep(0)                          # create_task: the pass starts on the next yield
    assert started == 2
    release.set()
    await ts._system_jobs_task


def test_scheduler_loop_does_not_await_system_jobs_inline():
    src = inspect.getsource(ts._scheduler_loop)
    assert "await tick_system_jobs" not in src
    assert "_spawn_system_jobs_tick(" in src
