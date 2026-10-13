"""MCP-registry route handlers must not run blocking work on the event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
a zero-await ``async def`` handler runs its whole body on the asyncio loop.
``check_mcp_registry_binary`` walks PATH via ``shutil.which`` (a stat per
PATH entry per name) -- under startup load that is enough to park the loop.
Every handler that awaits nothing must be plain ``def`` so Starlette runs it
on its worker threadpool.  Handlers that genuinely await the registry
manager / providers stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MODULE_PY = Path(__file__).parent.parent / "app" / "routes" / "mcp_registry_routes.py"


def test_no_mcp_registry_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MODULE_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their blocking "
        "work runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(MODULE_PY.read_text())
    assert len(handlers) >= 3, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["check_mcp_registry_binary"])
def test_known_hot_handlers_are_sync(name):
    """Called when the MCP registry modal opens."""
    handlers = {h[0]: h for h in _route_handlers(MODULE_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize(
    "name",
    ["uninstall_service_by_name", "get_service_detail", "test_registry_connection"],
)
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(MODULE_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
