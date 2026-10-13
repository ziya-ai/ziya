"""Memory API handlers must not touch the memory store on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a FastAPI handler that is
``async def`` but awaits nothing runs its whole body ON the event loop.  Every
handler in ``app/api/memory.py`` reads or writes the on-disk memory store,
mind-map or proposals file, and the Memory Browser polls several of them, so
under startup load each call parked the loop and no request was served.

Every handler in the file that awaits nothing must be plain ``def`` so
Starlette runs it on its worker threadpool.  Handlers that genuinely need the
loop (organize's ``asyncio.Lock`` + ``create_task``, the embedding backfill's
``await``) stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MEMORY_PY = Path(__file__).parent.parent / "app" / "api" / "memory.py"


def test_no_memory_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MEMORY_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their memory-store "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(MEMORY_PY.read_text())
    assert len(handlers) >= 29, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", [
    "get_memory_status", "list_all_memories", "get_mindmap", "list_proposals",
])
def test_known_polled_handlers_are_sync(name):
    """The ones the Memory Browser loads on open / polls."""
    handlers = {h[0]: h for h in _route_handlers(MEMORY_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["organize_memories", "backfill_embeddings"])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(MEMORY_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
