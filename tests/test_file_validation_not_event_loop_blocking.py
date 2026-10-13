"""File-validation API handlers must not stat files on the asyncio event loop.

``validate_files`` (``app/routes/file_validation.py``) runs ``os.path.exists``
for every requested path, against the project root and every
``ZIYA_INCLUDE_DIRS`` entry.  As a zero-await ``async def`` that disk I/O ran
inline on the event loop; under startup load (folder scans, AST indexers)
even small stat bursts park the loop and no request is served.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``:
every handler in the file that awaits nothing must be plain ``def`` so
Starlette runs it on its worker threadpool.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

FILE_VALIDATION_PY = Path(__file__).parent.parent / "app" / "routes" / "file_validation.py"


def test_no_file_validation_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(FILE_VALIDATION_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their file-stat "
        "I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(FILE_VALIDATION_PY.read_text())
    assert len(handlers) >= 1, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["validate_files"])
def test_known_hot_handlers_are_sync(name):
    """Called by the frontend whenever a context file selection is restored."""
    handlers = {h[0]: h for h in _route_handlers(FILE_VALIDATION_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"
