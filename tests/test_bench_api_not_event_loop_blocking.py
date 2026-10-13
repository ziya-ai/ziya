"""Bench API handlers must not read skill/state files on the asyncio event loop.

Attribution (two stall watchdog reports, 2026-10-09): the event loop was
inside ``pathlib.read_bytes`` under ``SkillStorage.list`` under
``bench_service.collect_items`` under ``get_bench`` (``app/api/bench.py``),
reached straight from FastAPI's ``run_endpoint_function``.  The handler was a
zero-await ``async def``, so every skill file was read and decrypted inline on
the loop.  The store is small (a few hundred KB) -- the stall is not volume
but contention: two folder scans and two AST indexers hold the GIL in bursts,
and a coroutine that never awaits cannot get out of their way.  The bench is
fetched on every conversation open, so the loop parked exactly when the user
was opening things.

Fourth instance of the defect class fixed in chats.py, task_runs.py,
folder_routes.py and the embedding backfill the same night; same guard.
Handlers that genuinely await (``get_browse``, ``put_place``) stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

BENCH_PY = Path(__file__).parent.parent / "app" / "api" / "bench.py"


def test_no_bench_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(BENCH_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their file "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(BENCH_PY.read_text())
    assert len(handlers) >= 10, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_bench", "get_detail"])
def test_known_polled_handlers_are_sync(name):
    """get_bench is fetched on every conversation open; it is what the watchdog caught."""
    handlers = {h[0]: h for h in _route_handlers(BENCH_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["get_browse", "put_place"])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(BENCH_PY.read_text())}
    assert handlers[name][2] and handlers[name][3] >= 1
