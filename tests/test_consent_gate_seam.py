"""
Seam test for the consent runtime (design/consent-runtime.md step 3).

Three halves that were each unit-tested must actually meet:

  execute_single_tool  --gate-->  ConsentLedger (on disk)  <--answer--  /api/consent

So this drives the REAL ``execute_single_tool`` against a tool the config
places in ``ask`` mode, observes that it parks (no dispatch yet), answers
through the REAL ``app.api.consent`` route handlers, and asserts that the
tool is then dispatched (approve) or refused with a ``_tool_result`` the
model will see (reject).  Also covers the reconnect handshake's
``GET /api/consent/open`` while the request is parked.

A test that mocked any one of the three would certify the halves and
miss the seam.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.utils import consent_gate as cg  # noqa: E402
from app.api import consent as consent_api  # noqa: E402


TOOL = "seam_write_tool"
CONV = "conv-seam-1"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    # Place exactly one tool in ask mode; everything else is ungated.
    monkeypatch.setattr(
        cg, "_consent_config",
        lambda: {"tools": {TOOL: {"default": cg.MODE_ASK}}},
    )
    # reset_for_tests also clears the grant cache, so a prior test's
    # "conversation" scope cannot leak into this one.
    yield
    cg.reset_for_tests()


def _ctx(tool_name: str, manager: MagicMock, conversation_id: str = CONV) -> ToolExecContext:
    return ToolExecContext(
        tool_id="tc-1",
        tool_name=tool_name,
        actual_tool_name=tool_name,
        args={"path": "x.txt"},
        all_tools=[],
        internal_tool_names=set(),
        mcp_manager=manager,
        project_root="/tmp",
        conversation_id=conversation_id,
        conversation=[],
        recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0,
        track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [],
        executor=MagicMock(),
    )


async def _collect_until_parked(gen, events, timeout=3.0):
    """Pull events until consent_opened appears (the gate is now waiting)."""
    async def pull():
        async for ev in gen:
            events.append(ev)
            if ev.get("type") == "consent_opened":
                return
    await asyncio.wait_for(pull(), timeout)


async def _finish(gen, events, timeout=3.0):
    async def pull():
        async for ev in gen:
            events.append(ev)
    await asyncio.wait_for(pull(), timeout)


def _request_id(events) -> str:
    opened = [e for e in events if e.get("type") == "consent_opened"]
    assert opened, f"gate never opened a consent; events: {[e.get('type') for e in events]}"
    return opened[-1]["request_id"]


@pytest.mark.asyncio
async def test_approve_via_api_dispatches_the_tool():
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    manager.call_tool = AsyncMock(return_value="wrote x.txt")
    ctx = _ctx(TOOL, manager)

    gen = execute_single_tool(ctx)
    events = []
    await _collect_until_parked(gen, events)
    rid = _request_id(events)

    # Parked: nothing dispatched yet, and the handshake sees it as open.
    assert manager.call_tool.await_count == 0
    open_now = await consent_api.list_open_consents(conversation_id=CONV)
    assert [r["request_id"] for r in open_now] == [rid]

    # Answer through the real route handler.
    res = await consent_api.answer_consent(
        rid, consent_api.ConsentAnswer(decision="approve", answered_by="tester"))
    assert res["settled"] is True

    await _finish(gen, events)
    types = [e.get("type") for e in events]
    assert "consent_answered" in types
    assert manager.call_tool.await_count == 1, "approve must dispatch the tool"
    results = [e for e in events if e.get("type") == "_tool_result"]
    assert results and "wrote x.txt" in str(results[-1]["result"])
    # Closed after the turn consumed the answer: no longer listed as open.
    assert await consent_api.list_open_consents(conversation_id=CONV) == []


@pytest.mark.asyncio
async def test_reject_via_api_refuses_without_dispatch():
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    manager.call_tool = AsyncMock(return_value="SHOULD NOT RUN")
    ctx = _ctx(TOOL, manager)

    gen = execute_single_tool(ctx)
    events = []
    await _collect_until_parked(gen, events)
    rid = _request_id(events)

    await consent_api.answer_consent(
        rid, consent_api.ConsentAnswer(decision="reject", answer="not now"))
    await _finish(gen, events)

    assert manager.call_tool.await_count == 0, "reject must never dispatch"
    results = [e for e in events if e.get("type") == "_tool_result"]
    assert results, "the model must still receive a tool_result for the tool_use"
    text = str(results[-1]["result"]).lower()
    assert "refus" in text or "reject" in text or "denied" in text
    assert "not now" in text


@pytest.mark.asyncio
async def test_ungated_tool_never_opens_a_consent():
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    manager.call_tool = AsyncMock(return_value="ok")
    ctx = _ctx("plain_read_tool", manager)

    events = []
    await _finish(execute_single_tool(ctx), events)
    assert "consent_opened" not in [e.get("type") for e in events]
    assert manager.call_tool.await_count == 1


@pytest.mark.asyncio
async def test_second_answer_is_reported_not_errored():
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    manager.call_tool = AsyncMock(return_value="ok")
    ctx = _ctx(TOOL, manager)

    gen = execute_single_tool(ctx)
    events = []
    await _collect_until_parked(gen, events)
    rid = _request_id(events)

    first = await consent_api.answer_consent(rid, consent_api.ConsentAnswer(decision="approve"))
    second = await consent_api.answer_consent(rid, consent_api.ConsentAnswer(decision="reject"))
    # First answer wins; the second call succeeds but returns the settled one.
    assert first["answer"]["decision"] == "approve"
    assert second["answer"]["decision"] == "approve"

    await _finish(gen, events)
    assert manager.call_tool.await_count == 1


@pytest.mark.asyncio
async def test_unknown_request_is_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await consent_api.answer_consent(
            "conv:nope:nope", consent_api.ConsentAnswer(decision="approve"))
    assert ei.value.status_code == 404
