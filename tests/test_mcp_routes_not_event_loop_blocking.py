"""MCP route handlers must not do their sync work on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a FastAPI handler that is
``async def`` but awaits nothing runs its whole body ON the loop.  In
``app/routes/mcp_routes.py`` those bodies read ``mcp_config.json`` and the
session-grant staging files, count tool-schema tokens for every connected
server, read/write the permissions and favorites stores -- and the frontend
polls ``/api/mcp/status`` and ``/api/mcp/shell-config`` from startup, while
the folder scans and AST indexers hold the GIL in bursts.

Every handler that awaits nothing must be plain ``def`` so Starlette runs it
on its worker threadpool.  Handlers that genuinely await the MCP manager
(initialize, restart, toggle, the registry calls) stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MCP_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "mcp_routes.py"


def test_no_mcp_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MCP_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their config/"
        "store I/O and token counting run ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(MCP_ROUTES_PY.read_text())
    assert len(handlers) >= 33, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", [
    "get_mcp_status",
    "get_shell_config",
    "get_mcp_tools",
    "get_builtin_tools_status",
])
def test_known_polled_handlers_are_sync(name):
    """The ones the frontend polls from startup and on opening MCP modals."""
    handlers = {h[0]: h for h in _route_handlers(MCP_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", [
    "initialize_mcp",
    "restart_shell_server",
    "apply_session_grant",
    "update_shell_config",
    "toggle_server",
    "get_registry_providers",
    "refresh_registry_provider",
    "get_registry_services",
    "search_registry_tools",
    "install_registry_service",
    "uninstall_registry_service",
    "get_installed_registry_services",
    "get_service_preview",
])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(MCP_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
