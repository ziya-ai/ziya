"""Bead backlog API handlers must not do storage I/O on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body on the loop.  ``get_backlog``
aggregates parked/abandoned beads across every chat in a project (disk
reads per chat) and ``set_bead_status`` reads and rewrites a chat's bead
tree; neither awaits anything, so both must be plain ``def`` so Starlette
runs them on its worker threadpool.  No handler here needs the loop.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

BACKLOG_PY = Path(__file__).parent.parent / "app" / "api" / "backlog.py"


def test_no_backlog_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(BACKLOG_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their storage "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(BACKLOG_PY.read_text())
    assert len(handlers) >= 1, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_backlog", "set_bead_status"])
def test_known_hot_handlers_are_sync(name):
    """The backlog browser's list fetch and the status transition."""
    handlers = {h[0]: h for h in _route_handlers(BACKLOG_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
