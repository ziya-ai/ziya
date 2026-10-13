"""Graph-route handlers must not load chats on the asyncio event loop.

``get_conversation_graph`` (``app/routes/graph_routes.py``) was a zero-await
``async def``: it reads the chat file through ``ChatStorage.get`` and builds
or loads the SQLite-cached graph -- all blocking disk I/O -- inline on the
loop.  Under startup load that parks the loop and no request is served.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
every handler in the file that awaits nothing must be plain ``def`` so
Starlette runs it on its worker threadpool.  No handler here needs the loop.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

GRAPH_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "graph_routes.py"


def test_no_graph_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(GRAPH_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their chat/graph "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(GRAPH_ROUTES_PY.read_text())
    assert len(handlers) >= 1, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_conversation_graph"])
def test_known_hot_handlers_are_sync(name):
    handlers = {h[0]: h for h in _route_handlers(GRAPH_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
