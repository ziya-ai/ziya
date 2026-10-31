"""
A waiting tool call gives way when the user sends a message.

Reported: the model ran job_start and then job_wait; a question the user
asked meanwhile was queued until the job ended, because queued feedback is
only delivered at a tool boundary and job_wait can last an hour.

Pinned at the seam the chat path uses (tool_execution._await_tool_result,
with the executor's peek_feedback_fn), against the real JobWaitTool and a
real job:
  * non-stop feedback ends job_wait within a few seconds with
    reason='user_message' and a 'next' to resume; the job is still running;
  * positive control: with no feedback the same wait runs to its timeout;
  * a stop directive still abandons the call (ToolStopRequested);
  * a tool that ignores the interrupt is unaffected.
"""
import asyncio
import os
import sys
import time
from types import SimpleNamespace

import pytest

from app.utils import tool_interrupt

os.environ.setdefault("ZIYA_TESTING", "1")


def _ctx(pending):
    return SimpleNamespace(conversation_id=None, tool_id="t", actual_tool_name="job_wait",
                           peek_feedback_fn=lambda: list(pending))


# ── unit ──────────────────────────────────────────────────────────────────

def test_requested_is_false_unbound_and_follows_the_bound_event():
    assert tool_interrupt.requested() is False
    ev, tok = tool_interrupt.bind()
    try:
        assert tool_interrupt.requested() is False
        ev.set()
        assert tool_interrupt.requested() is True
    finally:
        tool_interrupt.reset(tok)
    assert tool_interrupt.requested() is False


# ── executor seam, synthetic tools ────────────────────────────────────────

async def _cooperative(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if tool_interrupt.requested():
            return {"reason": "user_message"}
        await asyncio.sleep(0.05)
    return {"reason": "timeout"}


async def test_feedback_interrupts_a_cooperative_wait():
    from app.tool_execution import _await_tool_result
    pending = []

    async def later():
        await asyncio.sleep(0.3)
        pending.append({"type": "feedback", "message": "what is the status?"})
    asyncio.ensure_future(later())
    t0 = time.monotonic()
    res = await _await_tool_result(_cooperative(10), 30, _ctx(pending))
    assert res == {"reason": "user_message"}
    assert time.monotonic() - t0 < 3
    assert pending, "feedback is peeked, never drained, so the post-tool drain still delivers it"


async def test_positive_control_no_feedback_runs_to_completion():
    from app.tool_execution import _await_tool_result
    assert await _await_tool_result(_cooperative(0.5), 30, _ctx([])) == {"reason": "timeout"}


async def test_stop_directive_still_abandons_the_call():
    from app.tool_execution import ToolStopRequested, _await_tool_result
    pending = [{"type": "interrupt", "message": "stop"}]
    with pytest.raises(ToolStopRequested):
        await _await_tool_result(_cooperative(10), 30, _ctx(pending))


async def test_non_cooperative_tool_is_unaffected():
    from app.tool_execution import _await_tool_result
    pending = [{"type": "feedback", "message": "hi"}]

    async def plain():
        await asyncio.sleep(0.4)
        return 7
    assert await _await_tool_result(plain(), 30, _ctx(pending)) == 7


# ── end to end: the real job_wait ─────────────────────────────────────────

pytestmark_posix = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX only")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from app.context import set_project_root
    set_project_root(str(root))
    import app.jobs.manager as mgr_mod
    import app.jobs.policy as pol_mod
    mgr_mod._MANAGER = None
    pol_mod._POLICY = pol_mod._ShellPolicy()
    yield root
    try:
        for st in mgr_mod.get_job_manager().list(str(root)):
            if st["state"] == "running":
                mgr_mod.get_job_manager().kill(st["job_id"])
    except Exception:  # noqa: BLE001
        pass


@pytestmark_posix
async def test_job_wait_yields_to_a_user_message_and_job_keeps_running(proj):
    from app.mcp.tools.job_tools import JobStartTool, JobWaitTool
    from app.tool_execution import _await_tool_result
    r = await JobStartTool().execute(command="echo begin; sleep 30; echo end")
    assert r["ok"] is True
    pending = []

    async def later():
        await asyncio.sleep(1.0)
        pending.append({"type": "feedback", "message": "quick question"})
    asyncio.ensure_future(later())
    t0 = time.monotonic()
    w = await _await_tool_result(JobWaitTool().execute(job_id=r["job_id"], timeout_s=60), 90,
                                 _ctx(pending))
    assert time.monotonic() - t0 < 5, "the wait must give way, not run to its timeout"
    assert w["reason"] == "user_message" and w["interrupted_by_user"] is True
    assert w["still_running"] is True
    assert "job_wait(" in w["next"]
    from app.jobs.manager import get_job_manager
    assert get_job_manager().get(r["job_id"], str(proj)).state()["state"] == "running"


@pytestmark_posix
async def test_job_wait_positive_control_times_out_without_feedback(proj):
    from app.mcp.tools.job_tools import JobStartTool, JobWaitTool
    from app.tool_execution import _await_tool_result
    r = await JobStartTool().execute(command="sleep 30")
    w = await _await_tool_result(JobWaitTool().execute(job_id=r["job_id"], timeout_s=1.5), 30,
                                 _ctx([]))
    assert w["reason"] == "timeout" and not w.get("interrupted_by_user")


@pytestmark_posix
async def test_job_start_inline_wait_yields_too(proj):
    from app.mcp.tools.job_tools import JobStartTool
    from app.tool_execution import _await_tool_result
    pending = []

    async def later():
        await asyncio.sleep(1.0)
        pending.append({"type": "feedback", "message": "hey"})
    asyncio.ensure_future(later())
    t0 = time.monotonic()
    r = await _await_tool_result(JobStartTool().execute(command="sleep 30", wait_s=60), 90,
                                 _ctx(pending))
    assert time.monotonic() - t0 < 5
    assert r["job_id"] and r["reason"] == "user_message" and "job_wait(" in r["next"]
