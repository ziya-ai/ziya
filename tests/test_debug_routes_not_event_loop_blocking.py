"""Debug/telemetry route handlers must not do their work on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body ON the loop.  In
``app/routes/debug_routes.py`` that included ``/api/info`` (an STS
network round-trip for Bedrock credential status, plus a glob), and
``/api/debug/memstats`` (walks ``gc.get_objects()`` and deep-sizes caches)
-- seconds of loop stall exactly when someone is diagnosing a stall.

Every handler that awaits nothing must be plain ``def`` so Starlette runs
it on its worker threadpool.  Handlers that genuinely need the loop stay
``async`` and await: ``reset_mcp_state`` (awaits the request body and the
async counter reset, schedules reconnects with ``create_task``) and
``debug_threads`` (reads ``get_running_loop()._default_executor`` and
offloads the census via ``asyncio.to_thread``).
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

DEBUG_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "debug_routes.py"


def test_no_debug_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(DEBUG_ROUTES_PY.read_text())
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
    handlers = _route_handlers(DEBUG_ROUTES_PY.read_text())
    assert len(handlers) >= 10, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize(
    "name",
    ["get_system_info", "memstats", "get_cache_health_telemetry", "debug_mcp_state"],
)
def test_known_hot_handlers_are_sync(name):
    """/api/info is fetched by the Info panel and PDF export; memstats and
    the telemetry/MCP-state probes are polled while diagnosing."""
    handlers = {h[0]: h for h in _route_handlers(DEBUG_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["reset_mcp_state", "debug_threads"])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(DEBUG_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
