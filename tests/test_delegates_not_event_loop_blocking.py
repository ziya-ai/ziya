"""Delegate API handlers must not do their work on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body inline on the loop.  The two
status reads (``get_delegate_status``, ``get_swarm_budget``) are polled by
the frontend while a swarm runs, and on first touch ``get_delegate_manager``
constructs the manager and ``rehydrate()``s plan state from disk -- disk I/O
that would park the loop.  Every handler that awaits nothing must be plain
``def`` so Starlette runs it on its worker threadpool.  Handlers that
genuinely await DelegateManager coroutines (launch, cancel, retry, promote)
stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

DELEGATES_PY = Path(__file__).parent.parent / "app" / "api" / "delegates.py"


def test_no_delegates_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(DELEGATES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their "
        "manager/disk work runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(DELEGATES_PY.read_text())
    assert len(handlers) >= 5, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_delegate_status", "get_swarm_budget"])
def test_known_polled_handlers_are_sync(name):
    """The status reads the frontend polls during a swarm run."""
    handlers = {h[0]: h for h in _route_handlers(DELEGATES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize(
    "name",
    ["launch_delegates", "cancel_delegates", "retry_delegate", "promote_to_stub"],
)
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(DELEGATES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
