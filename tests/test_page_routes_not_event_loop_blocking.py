"""Page/UI route handlers must not run their bodies on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body ON the loop.  The page routes do
blocking work -- constructing ``Jinja2Templates`` and rendering
``index.html`` from disk (``/`` and every SPA passthrough), iterating plugin
config providers, ``os.path.exists`` for the favicon, and on ``/debug2`` a
synchronous boto3 STS call -- so under startup load the first page load
could park the loop and no request was served meanwhile.

Every handler in the file awaits nothing and must be plain ``def`` so
Starlette runs it on its worker threadpool.  The SPA passthroughs
(``/render``, ``/print``, ``/print-spike``, ``/debug``) used to
``await root(request)``; with ``root`` sync they call it directly, so none
legitimately stays ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

PAGE_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "page_routes.py"


def test_no_page_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(PAGE_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their template/"
        "disk I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(PAGE_ROUTES_PY.read_text())
    assert len(handlers) >= 8, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["root", "render_page", "print_page", "favicon"])
def test_startup_path_handlers_are_sync(name):
    """The app shell (first page load), the PDF-export passthroughs, favicon."""
    handlers = {h[0]: h for h in _route_handlers(PAGE_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


def test_no_handler_stays_async():
    """Nothing in this module needs the running loop."""
    handlers = _route_handlers(PAGE_ROUTES_PY.read_text())
    still_async = [h[0] for h in handlers if h[2]]
    assert still_async == [], f"unexpected async handlers: {still_async}"
