"""Bead API handlers must not read/write chat records on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await ``async def``
handler runs its whole body ON the loop.  ``get_beads`` (polled by the
frontend whenever a conversation is shown), ``resume_bead`` and
``fork_from_bead`` all load/save chat JSON from disk and await nothing, so
each disk read parked the loop under startup load.  Every handler in
``app/api/beads.py`` that awaits nothing must be plain ``def`` so Starlette
runs it on its worker threadpool.  None genuinely needs the loop.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

BEADS_PY = Path(__file__).parent.parent / "app" / "api" / "beads.py"


def test_no_beads_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(BEADS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their chat-file "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(BEADS_PY.read_text())
    assert len(handlers) >= 2, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_beads", "resume_bead", "fork_from_bead"])
def test_known_hot_handlers_are_sync(name):
    """get_beads is polled per conversation view; the other two do disk writes."""
    handlers = {h[0]: h for h in _route_handlers(BEADS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
