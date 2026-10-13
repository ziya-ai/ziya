"""Context API handlers must not do storage I/O on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
every handler in ``app/api/contexts.py`` awaits nothing and does synchronous
disk I/O (ProjectStorage lookup, ContextStorage reads/writes, ChatStorage
rewrites on delete).  As ``async def`` that work ran ON the event loop and no
other request was served meanwhile.  They must be plain ``def`` so Starlette
runs them on its worker threadpool.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

CONTEXTS_PY = Path(__file__).parent.parent / "app" / "api" / "contexts.py"


def test_no_context_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(CONTEXTS_PY.read_text())
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
    handlers = _route_handlers(CONTEXTS_PY.read_text())
    assert len(handlers) >= 4, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["list_contexts", "get_context", "delete_context"])
def test_known_hot_handlers_are_sync(name):
    """list/get are hit when a project's contexts load; delete rewrites every chat."""
    handlers = {h[0]: h for h in _route_handlers(CONTEXTS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
