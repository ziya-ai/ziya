"""Prompt-cache route handlers must not do cache-file I/O on the event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
``app/routes/cache_routes.py`` handlers call into ``get_prompt_cache()``,
whose stats/invalidate/clear paths stat and rewrite the on-disk cache file
synchronously.  As zero-await ``async def`` that I/O ran inline on the
asyncio loop.  Every handler that awaits nothing must be plain ``def`` so
Starlette runs it on its worker threadpool.  None genuinely needs the loop.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

CACHE_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "cache_routes.py"


def test_no_cache_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(CACHE_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their cache-file "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(CACHE_ROUTES_PY.read_text())
    assert len(handlers) >= 2, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_cache_stats", "invalidate_cache", "clear_cache"])
def test_known_handlers_are_sync(name):
    handlers = {h[0]: h for h in _route_handlers(CACHE_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
