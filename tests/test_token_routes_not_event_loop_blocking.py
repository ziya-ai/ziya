"""Token-count / cache-stats route handlers must not run on the event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
a zero-await ``async def`` handler runs its whole body ON the asyncio loop.
``/api/token-count`` is hit by the frontend token bar on every edit and
does tiktoken encoding plus a tool-history rewrite (CPU-bound, GIL-heavy);
``/api/cache-stats`` and ``/api/cache-test`` touch model config.  Every
handler in ``app/routes/token_routes.py`` that awaits nothing must be plain
``def`` so Starlette runs it on its worker threadpool.  The two handlers
that already offload their work with ``await asyncio.to_thread`` stay
``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

TOKEN_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "token_routes.py"


def test_no_token_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(TOKEN_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their work "
        "runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(TOKEN_ROUTES_PY.read_text())
    assert len(handlers) >= 4, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["count_tokens", "get_cache_stats", "test_cache_functionality"])
def test_known_hot_handlers_are_sync(name):
    """count_tokens is polled by the token bar; the cache ones are cheap GETs."""
    handlers = {h[0]: h for h in _route_handlers(TOKEN_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["get_accurate_token_counts", "context_estimate"])
def test_to_thread_offloading_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(TOKEN_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
