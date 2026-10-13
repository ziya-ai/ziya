"""Folder-route handlers must not touch the filesystem on the asyncio event loop.

Attribution (faulthandler dump of a live server, 2026-10-09): the event loop
was inside ``os.walk`` under ``get_default_included_folders``
(``app/routes/folder_routes.py``), a zero-await ``async def``.  That one was
fixed by offloading the walk, but the module still had eight more zero-await
handlers doing disk I/O inline: ``/folder-progress`` (polled during every
scan), ``/file`` and ``/save`` (file reads/writes), ``/api/browse-directory``
(listdir + stat per entry), ``/api/add-explicit-paths`` (directory scans and
project-storage writes), ``/api/clear-external-paths`` (project storage).

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
every handler that awaits nothing must be plain ``def`` so Starlette runs it
on its worker threadpool.  Handlers that genuinely await
(``asyncio.to_thread`` offloads, ``await api_get_folders()``) stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

FOLDER_ROUTES_PY = Path(__file__).parent.parent / "app" / "routes" / "folder_routes.py"


def test_no_folder_route_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(FOLDER_ROUTES_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their disk "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(FOLDER_ROUTES_PY.read_text())
    assert len(handlers) >= 13, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", [
    "get_folder_progress",      # polled throughout every folder scan
    "get_file",                 # file read
    "api_browse_directory",     # listdir + stat of every entry
    "api_add_explicit_paths",   # directory scans + project-storage write
])
def test_known_hot_handlers_are_sync(name):
    handlers = {h[0]: h for h in _route_handlers(FOLDER_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", [
    "get_folder",
    "get_default_included_folders",
    "api_external_dir",
    "get_folders_cached",
    "get_folders_with_accurate_tokens",
    "api_get_folders",
])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(FOLDER_ROUTES_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
