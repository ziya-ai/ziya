"""Transcription route handlers must not do blocking work on the event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body on the asyncio loop.
``transcription_status`` is polled by the frontend's microphone control and
calls ``importlib.util.find_spec`` (a sys.path filesystem walk), so it must
be plain ``def`` and run on Starlette's worker threadpool.

``install_transcription_backend`` and ``transcribe_audio`` genuinely await
(``asyncio.to_thread`` for pip / the model, ``UploadFile.read``/``close``)
and stay ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

MODULE_PY = Path(__file__).parent.parent / "app" / "routes" / "transcription_routes.py"


def test_no_transcription_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(MODULE_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their I/O "
        "runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(MODULE_PY.read_text())
    assert len(handlers) >= 2, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["transcription_status"])
def test_known_polled_handlers_are_sync(name):
    handlers = {h[0]: h for h in _route_handlers(MODULE_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


@pytest.mark.parametrize("name", ["install_transcription_backend", "transcribe_audio"])
def test_genuinely_awaiting_handlers_stay_async(name):
    handlers = {h[0]: h for h in _route_handlers(MODULE_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert handlers[name][2] and handlers[name][3] >= 1
