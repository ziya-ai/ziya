"""Consent API handlers must not read consent records on the asyncio event loop.

Same defect class, same guard, as ``test_chat_api_not_event_loop_blocking``
and ``test_task_runs_api_not_event_loop_blocking``: a zero-await
``async def`` handler runs its whole body ON the loop.  Every handler in
``app/api/consent.py`` reads (and ``answer_consent`` writes) JSON files
under the consent store -- ``list_open_consents`` globs and parses every
record, and the reconnect handshake calls it -- so each must be plain
``def`` and run on Starlette's worker threadpool.

Because handlers now run on threads, ``answer_consent`` serialises its
read-check-write under a module-level ``threading.Lock`` so two
simultaneous answers still resolve first-answer-wins; that is checked too.
"""
from pathlib import Path

import pytest

from tests.test_chat_api_not_event_loop_blocking import _route_handlers

CONSENT_PY = Path(__file__).parent.parent / "app" / "api" / "consent.py"


def test_no_consent_handler_is_a_zero_await_coroutine():
    handlers = _route_handlers(CONSENT_PY.read_text())
    offenders = [
        (name, ln) for name, ln, is_async, n_awaits in handlers
        if is_async and n_awaits == 0
    ]
    assert offenders == [], (
        "These handlers are 'async def' but await nothing, so their consent-"
        "store I/O runs ON the event loop:\n"
        + "\n".join(f"  L{ln} {name}" for name, ln in offenders)
        + "\n\nMake them plain 'def' or wrap the work in asyncio.to_thread."
    )


def test_scan_is_not_vacuous():
    handlers = _route_handlers(CONSENT_PY.read_text())
    assert len(handlers) >= 2, f"only {len(handlers)} handlers found; detector broken?"


@pytest.mark.parametrize("name", ["list_open_consents", "get_consent", "answer_consent"])
def test_known_hot_handlers_are_sync(name):
    """list_open_consents is on the reconnect path; get is polled by panels."""
    handlers = {h[0]: h for h in _route_handlers(CONSENT_PY.read_text())}
    assert name in handlers, f"{name} not found -- renamed?"
    assert not handlers[name][2], f"{name} must be plain 'def'"


def test_concurrent_answers_are_first_answer_wins(tmp_path):
    """Threadpool dispatch must not let two answers both land."""
    import threading

    from app.api import consent as consent_api
    from app.utils import consent_gate
    from app.utils.consent_ledger import ConsentRecord

    consent_gate.reset_for_tests(tmp_path)
    try:
        rid = "conv:c1:t1"
        consent_gate.ledger().store.open(
            ConsentRecord(request_id=rid, owner_id="c1", payload={}, opened_at=0)
        )
        results = []
        barrier = threading.Barrier(8)

        def answer(i):
            barrier.wait()
            body = consent_api.ConsentAnswer(
                decision="approve" if i % 2 else "reject", answered_by=f"u{i}",
            )
            results.append(consent_api.answer_consent(rid, body))

        threads = [threading.Thread(target=answer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        landed = consent_gate.ledger().get(rid).answer
        assert len(results) == 8
        assert all(r["answer"] == landed for r in results)
    finally:
        consent_gate.reset_for_tests(None)
