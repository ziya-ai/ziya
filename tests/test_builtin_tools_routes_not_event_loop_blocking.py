"""Builtin-tools route handlers must not run their bodies on the event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await ``async def``
handler runs inline on the asyncio loop.  ``/builtin-tools/status`` is fetched
when the MCP/tools settings open and instantiates every builtin tool class
and probes pcap dependencies (imports) -- under startup GIL contention that
parks the loop.  Every handler here awaits nothing, so all must be plain
``def`` and run on Starlette's worker threadpool.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MODULE_PY = Path(__file__).parent.parent / "app" / "routes" / "builtin_tools_routes.py"


def test_no_builtin_tools_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MODULE_PY.read_text())
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
    handlers = _route_handlers(MODULE_PY.read_text())
    assert len(handlers) >= 2, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize(
    "name",
    ["get_builtin_tools_status", "check_category_dependencies", "toggle_builtin_tool_category"],
)
def test_known_hot_handlers_are_sync(name):
    """Status is fetched whenever the tools settings open; the others probe imports."""
    handlers = {h[0]: h for h in _route_handlers(MODULE_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
