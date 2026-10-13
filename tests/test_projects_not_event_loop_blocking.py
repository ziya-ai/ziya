"""Project API handlers must not do project-store I/O on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: every handler in
``app/api/projects.py`` reads or writes the project store / chat dirs
(``ProjectStorage``, ``_count_chats``, template files) and awaits nothing.
As zero-await ``async def`` that disk work ran inline on the loop; the
startup path (``/startup``, ``/current``, ``/last-accessed``, list) hits it
while folder scans and AST indexers are contending for the GIL.  Every such
handler must be plain ``def`` so Starlette runs it on its worker threadpool.
No handler in this module genuinely needs the loop.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

PROJECTS_PY = Path(__file__).parent.parent / "app" / "api" / "projects.py"


def test_no_project_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(PROJECTS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their project-store "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(PROJECTS_PY.read_text())
    assert len(handlers) >= 12, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize(
    "name",
    ["get_startup_info", "get_current_project", "get_last_accessed_project", "list_projects"],
)
def test_startup_path_handlers_are_sync(name):
    """The ones the frontend calls on boot, while startup scans hold the GIL."""
    handlers = {h[0]: h for h in _route_handlers(PROJECTS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
