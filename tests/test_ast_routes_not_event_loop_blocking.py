"""AST routes must not generate the AST context on the asyncio event loop.

Attribution (two stall watchdog reports, 2026-10-09): the event loop was
inside ``generate_ast_context`` under ``get_ast_token_count`` under
``get_status`` -- and, in the next report, under ``get_resolution_estimates``
under ``get_resolutions`` -- both in ``app/routes/ast_routes.py``.  Each is a
zero-await ``async def``, and each regenerates the full AST context (one
``relpath`` + symbol/dependency extraction per indexed file; 7300 files on
one project) inline on the loop.  The frontend polls ``/api/ast/status``
while indexing runs, so this fired repeatedly during exactly the window the
two AST indexers and two folder scans were holding the GIL.

Fifth instance of the defect class (chats, task_runs, bench, folder_routes,
embedding backfill); same guard, same remedy: plain ``def`` so Starlette
dispatches to its worker threadpool.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

AST_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "ast_routes.py"


def test_no_ast_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(AST_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their AST "
        "context generation runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(AST_ROUTES_PY.read_text())
    assert len(handlers) >= 3, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_status", "get_resolutions"])
def test_polled_handlers_are_sync(name):
    """The two the watchdog caught; /status is polled while indexing runs."""
    handlers = {h[0]: h for h in _route_handlers(AST_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
