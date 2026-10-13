"""Task-card API handlers must not read card/approval files on the asyncio loop.

Attribution (two stall watchdog reports, 2026-10-09): the event loop was
inside ``open()`` under ``scope_approvals.get_record`` -- and, in the next
report, under the enterprise ``ConfigProvider.get_defaults`` reading its
config file -- both under ``_escalation_rows`` under ``get_signature_summary``
(``app/api/task_cards.py``).  The handler is a zero-await ``async def`` that
walks every card in the deck and opens every approval record, and the header
button re-polls it on every window focus.

Sixth instance of the defect class (chats, task_runs, bench, folder_routes,
ast_routes, embedding backfill); same guard.  ``launch_task_card`` awaits the
launch helper and stays ``async``.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

TASK_CARDS_PY = Path(__file__).parent.parent / "app" / "api" / "task_cards.py"


def test_no_task_card_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(TASK_CARDS_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their card/"
        "approval file I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(TASK_CARDS_PY.read_text())
    assert len(handlers) >= 12, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["get_signature_summary", "list_task_cards", "get_card_scope_status"])
def test_polled_handlers_are_sync(name):
    handlers = {h[0]: h for h in _route_handlers(TASK_CARDS_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


def test_launch_stays_async():
    handlers = {h[0]: h for h in _route_handlers(TASK_CARDS_PY.read_text())}
    assert handlers["launch_task_card"][2] and handlers["launch_task_card"][3] >= 1
