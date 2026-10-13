"""A consent hold inside a task reaches the run's event stream.

The consent gate yields a ``consent_opened`` chunk when it parks a tool
call on the ledger.  task_executor's chunk loop had no branch for it, so
the chunk was dropped and no sink -- not the CLI, not the GUI -- could say
the run was waiting.  Now it is forwarded as ``task_hold`` (plus a
``task_progress`` note), and the CLI sink renders it.
"""

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.cli_card_runner import _StdoutSink
from app.models.task_card import Block


class _HoldExecutor:
    def __init__(self, *a, **kw):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "consent_opened", "request_id": "conv:r1:toolu_1",
               "payload": {"tool": "git", "op": "commit", "args": {}}}
        yield {"type": "consent_answered", "request_id": "conv:r1:toolu_1",
               "answer": {"decision": "approve"}}
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.mark.asyncio
async def test_consent_chunks_become_task_hold_events():
    events = []

    async def fake_push(run_id, evt):
        events.append(evt)

    block = Block(block_type="task", id="t1", name="T", instructions="do")
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _HoldExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.task_run_stream_relay.safe_push", fake_push):
        art = await task_executor.execute_task_block(block, run_id="r1")
    holds = [e for e in events if e["type"] == "task_hold"]
    assert [h["state"] for h in holds] == ["opened", "answered"]
    assert holds[0]["tool"] == "git" and holds[0]["op"] == "commit"
    assert holds[0]["request_id"] == "conv:r1:toolu_1"
    assert any(e["type"] == "task_progress" and "waiting for approval" in e["note"]
               for e in events)
    assert not art.failed


@pytest.mark.asyncio
async def test_cli_sink_prints_the_hold(capsys):
    sink = _StdoutSink()
    await sink.send_json({"type": "task_hold", "state": "opened", "tool": "git",
                          "op": "commit", "request_id": "conv:r1:toolu_1"})
    await sink.send_json({"type": "task_hold", "state": "answered", "tool": "git",
                          "op": "commit", "answer": {"decision": "reject"}})
    await sink.send_json({"type": "ask_opened", "question": "Proceed to prod?"})
    out = capsys.readouterr().out
    assert "waiting for approval: git commit" in out
    assert "conv:r1:toolu_1" in out
    assert "git commit: reject" in out
    assert "Proceed to prod?" in out
