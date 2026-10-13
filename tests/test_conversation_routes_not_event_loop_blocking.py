"""Conversation/folder route handlers must not do file I/O on the event loop.

``app/routes/conversation_routes.py`` handlers read and rewrite
``.ziya/folders.json`` / ``.ziya/conversations.json`` (encryption-aware,
via ``read_json_file`` / ``write_json_file``).  They were zero-await
``async def``, so that disk I/O ran inline on the asyncio event loop and
stalled every other request while it ran.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
every handler in the file that awaits nothing must be plain ``def`` so
Starlette runs it on its worker threadpool.  No handler here genuinely
needs the loop, so none stays ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MODULE_PY = Path(__file__).parent.parent / "app" / "routes" / "conversation_routes.py"


def test_no_conversation_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MODULE_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their JSON-store "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(MODULE_PY.read_text())
    assert len(handlers) >= 2, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["create_folder", "create_conversation", "move_conversation"])
def test_json_store_handlers_are_sync(name):
    handlers = {h[0]: h for h in _route_handlers(MODULE_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
