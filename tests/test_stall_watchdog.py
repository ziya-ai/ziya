"""The stall watchdog (app/utils/stall_watchdog.py) reports the two stall
shapes seen on the live server and stays quiet otherwise.

  1. Loop parked: a coroutine blocks the event loop synchronously.  The
     report names the loop as the trigger and its stack shows the blocker.
  2. Executor saturated: every default-executor worker is busy and work is
     queued, while the loop itself stays responsive.  Only the census can
     show this -- a stack dump alone reads as "everything is idle".

Each is also paired with its negative (no report when the condition does
not hold), and one report per episode is pinned.
"""
import asyncio
import concurrent.futures
import threading
import time

import pytest

from app.utils import stall_watchdog as sw


def _collect():
    reports = []
    return reports, reports.append


@pytest.mark.asyncio
async def test_reports_parked_event_loop_with_its_stack():
    loop = asyncio.get_running_loop()
    reports, emit = _collect()
    wd = sw.StallWatchdog(loop, threshold_s=0.2, emit=emit, ping_interval_s=0.05,
                          main_ident=threading.get_ident()).start()
    try:
        await asyncio.sleep(0.3)           # responsive: nothing reported
        assert reports == []

        def _block_loop_inline():
            time.sleep(0.8)                # the defect: sync work on the loop

        _block_loop_inline()
        await asyncio.sleep(0.3)
    finally:
        wd.stop()
    assert len(reports) == 1, reports      # one report for one episode
    text = reports[0]
    assert "event loop did not run a callback" in text
    assert "(event loop)" in text
    assert "_block_loop_inline" in text    # the stack names the blocker


@pytest.mark.asyncio
async def test_reports_saturated_default_executor_while_loop_is_fine():
    loop = asyncio.get_running_loop()
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(ex)
    reports, emit = _collect()
    wd = sw.StallWatchdog(loop, threshold_s=0.2, emit=emit, ping_interval_s=0.05).start()
    release = threading.Event()
    try:
        # Fill both workers and queue a third item; the loop keeps running.
        futs = [asyncio.ensure_future(asyncio.to_thread(release.wait, 5)) for _ in range(3)]
        await asyncio.sleep(0.6)
        assert len(reports) == 1, reports
        assert "default executor saturated" in reports[0]
        assert "2/2 workers busy, 1 queued" in reports[0]
        assert "event loop did not run" not in reports[0]
    finally:
        release.set()
        await asyncio.gather(*futs)
        wd.stop()
        ex.shutdown(wait=True)


def test_executor_census_before_any_to_thread():
    loop = asyncio.new_event_loop()
    try:
        assert sw.executor_census(loop)["created"] == 0
    finally:
        loop.close()


def test_start_from_env(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        monkeypatch.delenv("ZIYA_STALL_DUMP", raising=False)
        assert sw.start_from_env(loop) is None
        monkeypatch.setenv("ZIYA_STALL_DUMP", "nope")
        assert sw.start_from_env(loop) is None
        monkeypatch.setenv("ZIYA_STALL_DUMP", "5")
        wd = sw.start_from_env(loop)
        assert wd is not None and wd.threshold == 5.0
        wd.stop()
    finally:
        loop.close()
