"""Regression: /api/shadow/attached must not run blocking work on the event loop.

``attached_sessions()`` does a registry glob plus up to two 0.3s AF_UNIX
round-trips per attached session. The ``/api/shadow/attached`` route is
``async def`` and is polled by every open conversation window every 5s
(ShadowSessionChip, POLL_MS=5000). If it calls ``attached_sessions()`` inline,
that blocking I/O runs directly on the asyncio event loop and — under any
window count, and badly when a shadow host is hung and each socket op burns its
full timeout — stalls every other request in the process, including the
project-switch fetches that populate the sidebar. It must be offloaded to a
worker thread.

This asserts the offload structurally: the helper must execute on a DIFFERENT
thread than the event loop, and the awaited call must actually run it (positive
assertion that the path was exercised).
"""
import asyncio
import threading

import pytest

from app.routes import shadow_routes


@pytest.mark.asyncio
async def test_attached_offloads_blocking_helper_to_worker_thread(monkeypatch):
    loop_thread_id = threading.get_ident()
    ran_on: dict = {}

    def fake_attached_sessions(conversation_id):
        # Record which thread the (blocking) helper actually ran on.
        ran_on["thread_id"] = threading.get_ident()
        ran_on["conversation_id"] = conversation_id
        return [{"session_id": "s1"}]

    # attached() imports attached_sessions from app.shadow.context at call time.
    import app.shadow.context as shadow_context
    monkeypatch.setattr(shadow_context, "attached_sessions", fake_attached_sessions)

    result = await shadow_routes.attached(conversation_id="conv-abc")

    # Positive: the helper was actually invoked with our conversation id and
    # its result was returned — the path ran, the test isn't vacuous.
    assert ran_on.get("conversation_id") == "conv-abc"
    assert result == {"conversation_id": "conv-abc", "sessions": [{"session_id": "s1"}]}

    # The invariant: the blocking helper ran on a worker thread, NOT the loop.
    assert "thread_id" in ran_on, "attached_sessions was never called"
    assert ran_on["thread_id"] != loop_thread_id, (
        "attached_sessions ran on the event-loop thread — it must be offloaded "
        "via asyncio.to_thread so its blocking registry glob + AF_UNIX "
        "round-trips do not stall the loop for every other request"
    )
