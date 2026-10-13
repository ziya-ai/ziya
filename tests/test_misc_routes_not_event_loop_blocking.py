"""Misc route handlers must not do blocking work on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
a zero-await ``async def`` handler runs its whole body on the loop.  In
``app/routes/misc_routes.py`` that was ``analyze_pcap`` (scapy parses the
whole capture file synchronously), ``pcap_status`` (scapy import probe) and
``supported_document_types`` (lazy extractor import).  Every handler that
awaits nothing must be plain ``def`` so Starlette runs it on its worker
threadpool.  Handlers that genuinely await (request body reads, the chat
turn relay, the to_thread-wrapped document extraction) stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MISC_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "misc_routes.py"


def test_no_misc_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MISC_ROUTES_PY.read_text())
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
    handlers = _route_handlers(MISC_ROUTES_PY.read_text())
    assert len(handlers) >= 6, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["analyze_pcap", "pcap_status", "supported_document_types"])
def test_known_blocking_handlers_are_sync(name):
    """pcap analysis parses whole capture files; the other two are polled on load."""
    handlers = {h[0]: h for h in _route_handlers(MISC_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", [
    "extract_document", "update_dynamic_tools", "abort_stream", "retry_throttled_request",
])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(MISC_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
