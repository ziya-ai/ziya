"""Handoff API handlers must not read/write chat records on the event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
the handlers in ``app/api/handoff.py`` resolve the conversation's home
(``resolve_chat_home``), read and rewrite the chat JSON and the bead tree --
all blocking disk I/O.  As zero-await ``async def`` they ran inline on the
asyncio loop and stalled every other request while they did.  Every handler
that awaits nothing must be plain ``def`` so Starlette runs it on its worker
threadpool.  None of them needs the loop, so none stays ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

HANDOFF_PY = Path(__file__).parent.parent / "app" / "api" / "handoff.py"


def test_no_handoff_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(HANDOFF_PY.read_text())
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
    handlers = _route_handlers(HANDOFF_PY.read_text())
    assert len(handlers) >= 3, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_handoff_state", "edit_draft", "commit_handoff"])
def test_known_hot_handlers_are_sync(name):
    """get_handoff_state is read whenever the drawer/card renders; the
    other two rewrite the chat record."""
    handlers = {h[0]: h for h in _route_handlers(HANDOFF_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
