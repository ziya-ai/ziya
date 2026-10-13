"""Native drag helper API handlers must not block the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body ON the event loop.  Both handlers
in ``app/api/drag_helper.py`` do blocking work -- ``is_supported()`` imports
AppKit (a heavy pyobjc import the first time), and ``arm`` may spawn the
helper subprocess and writes to its stdin pipe under a ``threading.Lock``.
``arm_drag`` fires on every pointerdown over a diagram or table, so it must
run on Starlette's worker threadpool: every handler here is plain ``def``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

DRAG_HELPER_PY = Path(__file__).parent.parent / "app" / "api" / "drag_helper.py"


def test_no_drag_helper_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(DRAG_HELPER_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their blocking "
        "work runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(DRAG_HELPER_PY.read_text())
    assert len(handlers) >= 1, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["arm_drag", "drag_status"])
def test_known_hot_handlers_are_sync(name):
    """arm_drag fires on every pointerdown; drag_status is probed at startup."""
    handlers = {h[0]: h for h in _route_handlers(DRAG_HELPER_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
