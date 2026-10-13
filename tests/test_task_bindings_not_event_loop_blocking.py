"""Task-binding API handlers must not do storage I/O on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body ON the event loop.
``list_task_bindings`` is fetched for every chat the frontend opens and
reads the bindings file, every referenced run record (decrypt + parse) and,
on the empty-local path, stats a bindings file under every project -- all
inline on the loop while it was ``async def``.  Every handler that awaits
nothing must be plain ``def`` so Starlette runs it on its worker threadpool.
The two handlers that genuinely await ``_launch_run_for_card`` (create and
launch-staged) stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

TASK_BINDINGS_PY = Path(__file__).parent.parent / "app" / "api" / "task_bindings.py"


def test_no_task_binding_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(TASK_BINDINGS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their binding/"
        "run-file I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(TASK_BINDINGS_PY.read_text())
    assert len(handlers) >= 3, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["list_task_bindings", "delete_task_binding"])
def test_known_hot_handlers_are_sync(name):
    """list is fetched per opened chat; delete is the other zero-await one."""
    handlers = {h[0]: h for h in _route_handlers(TASK_BINDINGS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["create_task_binding", "launch_staged_binding"])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(TASK_BINDINGS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
