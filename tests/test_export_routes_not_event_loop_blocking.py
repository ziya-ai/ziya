"""Export route handlers must not do blocking work on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body ON the loop, so its plugin calls
(``get_active_config_providers`` / ``get_export_providers`` and their
``get_defaults`` / ``get_target_info``) park every other request.  Every
handler in ``app/routes/export_routes.py`` that awaits nothing must be plain
``def`` so Starlette runs it on its worker threadpool.  The four export
handlers genuinely await the renderers/providers and stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

EXPORT_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "export_routes.py"


def test_no_export_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(EXPORT_ROUTES_PY.read_text())
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
    handlers = _route_handlers(EXPORT_ROUTES_PY.read_text())
    assert len(handlers) >= 4, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_export_targets"])
def test_known_polled_handlers_are_sync(name):
    """Fetched whenever the export modal opens; walks plugin providers."""
    handlers = {h[0]: h for h in _route_handlers(EXPORT_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize(
    "name", ["export_rendered", "export_to_target", "export_pdf", "export_document"]
)
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(EXPORT_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
