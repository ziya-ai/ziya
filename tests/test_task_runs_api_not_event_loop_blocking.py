"""Task-run API handlers must not read run records on the asyncio event loop.

Attribution (stall watchdog report, 2026-10-09, after py-spy and
faulthandler both crashed the server): the event loop was inside
``pathlib.read_bytes`` under ``TaskRunStorage.list`` under
``list_task_runs`` (``app/api/task_runs.py``), reached straight from
FastAPI's ``run_endpoint_function``.  The handler was a zero-await
``async def``, so reading, decrypting and parsing every run file -- 508
files, 483 MB on the reporting install -- ran inline on the loop, and the
frontend polls that endpoint.  For the whole read no request was served:
the "nothing, then everything at once" burst in the server log, and the
config modals that would not open for minutes after start.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
every handler in the file that awaits nothing must be plain ``def`` so
Starlette runs it on its worker threadpool.  Handlers that genuinely await
(cancel's ``live_runs.abort``, the two resume-from launches) stay ``async``.
"""
import ast
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

TASK_RUNS_PY = Path(__file__).parent.parent / "app" / "api" / "task_runs.py"


def test_no_task_run_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(TASK_RUNS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their run-file "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(TASK_RUNS_PY.read_text())
    assert len(handlers) >= 15, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["list_task_runs", "get_run_status_index", "get_task_run"])
def test_known_polled_handlers_are_sync(name):
    """The ones the frontend polls, and the one the watchdog caught."""
    handlers = {h[0]: h for h in _route_handlers(TASK_RUNS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["cancel_task_run", "resume_run_from_block"])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(TASK_RUNS_PY.read_text())}
    assert handlers[name][2] and handlers[name][3] >= 1
