"""Token API handlers must not do project/skill/file I/O on the asyncio event loop.

``calculate_tokens`` (``app/api/tokens.py``) loads the project record, every
requested context and skill (decrypting skill files), and tokenizes every
selected file from disk.  As a zero-await ``async def`` all of that ran ON the
event loop; it is recomputed whenever the file/context selection changes, so
under startup GIL contention (folder scans + AST indexers) it would park the
loop and stall every other request.  Same defect class fixed in chats.py,
task_runs.py, bench.py, task_cards.py and folder_routes.py; same guard.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

TOKENS_PY = Path(__file__).parent.parent / "app" / "api" / "tokens.py"


def test_no_tokens_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(TOKENS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their file "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(TOKENS_PY.read_text())
    assert len(handlers) >= 1, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["calculate_tokens"])
def test_known_polled_handlers_are_sync(name):
    """calculate_tokens is re-run on every selection change; it reads files from disk."""
    handlers = {h[0]: h for h in _route_handlers(TOKENS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
