"""
A tool call holds its chat turn, so a disconnect during a long tool wait
does not cancel the turn.

Reported: a ``job_start`` + ``job_wait`` turn; the user's ssh tunnel and
browser died during the wait.  With no subscriber, the relay's 60 s
disconnect grace timer cancelled the turn mid-wait; the finished turn was
retained for 5 min and then dropped, so the reloaded tab showed only the
unanswered question (orange bar, Retry).

The seam pinned here is tool_execution._await_tool_result -> relay hold:
both tool dispatch branches (builtin and MCP) go through that function.
Positive controls: the same unsubscribed turn IS cancelled when the source
waits outside a tool, and the hold is released once the tool returns.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.agents.chat_turn_relay as relay
from app.tool_execution import _await_tool_result


@pytest.fixture(autouse=True)
def _clean():
    relay._reset_for_tests()
    yield
    relay._reset_for_tests()


def _ctx(conv: str, tool_id: str = "t1"):
    return SimpleNamespace(conversation_id=conv, tool_id=tool_id, peek_feedback_fn=None)


async def _drop_subscriber(conv: str):
    agen = relay.subscribe(conv)
    await agen.__anext__()
    await agen.aclose()


async def _wait_done(conv: str, timeout: float = 2.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        st = relay.status(conv)
        if st and not st["active"]:
            return st
        await asyncio.sleep(0.005)
    raise AssertionError("turn did not finish")


async def test_unsubscribed_turn_survives_a_tool_wait_longer_than_grace():
    seen = {}

    async def source():
        yield 'data: {"content": "a"}\n\n'

        async def slow_tool():
            await asyncio.sleep(0.15)       # several grace periods
            return {"ok": True}

        seen["result"] = await _await_tool_result(slow_tool(), 5, _ctx("conv-job"))
        seen["held_after"] = relay.status("conv-job")["held"]
        yield 'data: {"content": "b"}\n\n'

    with patch.object(relay, "_grace_seconds", return_value=0.02):
        await relay.start_turn("conv-job", source())
        await _drop_subscriber("conv-job")   # tunnel dies
        st = await _wait_done("conv-job")
    assert st["cancelled"] is False, "a tool wait must not be grace-cancelled"
    assert seen["result"] == {"ok": True}
    assert seen["held_after"] is False, "hold must be released after the tool"


async def test_positive_control_unsubscribed_wait_outside_a_tool_is_cancelled():
    async def source():
        yield 'data: {"content": "a"}\n\n'
        await asyncio.sleep(0.15)            # not a tool: no hold
        yield 'data: {"content": "b"}\n\n'

    with patch.object(relay, "_grace_seconds", return_value=0.02):
        await relay.start_turn("conv-bare", source())
        await _drop_subscriber("conv-bare")
        st = await _wait_done("conv-bare")
    assert st["cancelled"] is True


async def test_tool_without_a_relay_turn_still_runs():
    async def tool():
        return 42
    assert await _await_tool_result(tool(), 5, _ctx("no-such-turn")) == 42
    assert await _await_tool_result(tool(), 5, _ctx(None)) == 42


async def test_hold_released_when_tool_raises():
    gate = asyncio.Event()

    async def source():
        yield 'data: {"content": "a"}\n\n'
        await gate.wait()
        yield 'data: {"content": "b"}\n\n'

    await relay.start_turn("conv-err", source())

    async def bad():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _await_tool_result(bad(), 5, _ctx("conv-err"))
    assert relay.status("conv-err")["held"] is False
    gate.set()
    await _wait_done("conv-err")
