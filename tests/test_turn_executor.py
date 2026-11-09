"""The chat-turn path must not queue behind scan work on the default executor.

Reproduces the startup stall seen in the server log: the default
``asyncio.to_thread`` pool (min(32, cpu+4) threads) fills with
``get_ignored_patterns`` waiters parked on a 60 s build lock, and the
turn's own ``to_thread`` calls wait for the scan deadline before the
user's message reaches the model.
"""

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.utils.turn_executor import run_on_turn_executor, turn_executor, TURN_EXECUTOR_WORKERS


def _default_pool_size() -> int:
    return min(32, (os.cpu_count() or 1) + 4)


@pytest.mark.asyncio
async def test_turn_work_runs_while_default_executor_is_saturated():
    """Fill the default executor with blocked workers; turn work still runs promptly."""
    loop = asyncio.get_running_loop()
    # Use an explicit default executor of the documented size so the test is
    # deterministic regardless of what other tests did to the loop.
    n = _default_pool_size()
    default_pool = ThreadPoolExecutor(max_workers=n)
    loop.set_default_executor(default_pool)
    release = threading.Event()

    def _parked():
        # Stand-in for a get_ignored_patterns caller waiting on the build lock.
        release.wait(timeout=30)

    hogs = [asyncio.to_thread(_parked) for _ in range(n)]
    hog_tasks = [asyncio.ensure_future(h) for h in hogs]
    await asyncio.sleep(0.2)  # let every hog claim a thread

    try:
        # Control: a to_thread call now has no free worker.
        probe = asyncio.ensure_future(asyncio.to_thread(lambda: "late"))
        await asyncio.sleep(0.3)
        assert not probe.done(), "default executor was not saturated; test setup is wrong"

        # The turn pool is independent and answers immediately.
        t0 = time.monotonic()
        result = await asyncio.wait_for(
            run_on_turn_executor(lambda a, k=None: (a, k, threading.current_thread().name), 1, k=2),
            timeout=2.0,
        )
        elapsed = time.monotonic() - t0
        assert result[:2] == (1, 2)
        assert result[2].startswith("ziya-turn"), result[2]
        assert elapsed < 1.0, f"turn work took {elapsed:.2f}s while default pool was busy"
    finally:
        release.set()
        await asyncio.gather(*hog_tasks, probe, return_exceptions=True)
        default_pool.shutdown(wait=False)


def test_turn_executor_is_small_and_named():
    ex = turn_executor()
    assert isinstance(ex, ThreadPoolExecutor)
    assert ex._max_workers == TURN_EXECUTOR_WORKERS
    assert ex._thread_name_prefix == "ziya-turn"


@pytest.mark.asyncio
async def test_run_on_turn_executor_propagates_exceptions():
    def _boom():
        raise ValueError("turn failure")

    with pytest.raises(ValueError, match="turn failure"):
        await run_on_turn_executor(_boom)


@pytest.mark.asyncio
async def test_run_on_turn_executor_carries_request_context(tmp_path):
    """The request-scoped project root must be visible on the turn pool.

    build_messages_for_streaming runs on this pool and reads
    app.context.get_project_root_or_none() to emit CurrentProjectRoot /
    CurrentWorkingDirectory and resolve the write policy.  A bare
    run_in_executor gives the worker an empty contextvars context, so the
    header fell back to os.getcwd() (the server's launch directory) while
    the tools, which re-assert the root in their own task, used the real
    project root.
    """
    from app.context import get_project_root_or_none, set_project_root

    root = str(tmp_path)

    async def _in_fresh_context():
        set_project_root(root)
        # Positive control: the value is set in the calling context.
        assert get_project_root_or_none() == root
        seen = await run_on_turn_executor(get_project_root_or_none)
        assert seen == root, f"turn pool saw project root {seen!r}, expected {root!r}"
        thread = await run_on_turn_executor(lambda: threading.current_thread().name)
        assert thread.startswith("ziya-turn")

    # A task gets its own copy of the context, so set_project_root above
    # does not leak into other tests.
    await asyncio.get_running_loop().create_task(_in_fresh_context())
