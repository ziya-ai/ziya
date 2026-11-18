"""A tool call in a task run is visible while it is in flight.

``task_tool_call`` is emitted after the tool returns, so a run view could
not show a call that was still running or what it was called with.  The
executor's ``tool_start`` chunk now becomes a ``task_tool_start`` event,
and ``task_tool_call`` carries the argument header, an error flag and the
duration.  The frontend pairs the two by ``tool_id``
(frontend/src/components/TaskCard/runTranscript.ts).

Drives execute_task_block with a fake StreamingToolExecutor, as
tests/test_task_executor_progress_events.py does.
"""
from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.agents import task_run_stream_relay as relay
from app.models.task_card import Block


def _task() -> Block:
    return Block(block_type="task", id="task-1", name="T", instructions="do it")


class _FakeExecutor:
    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "text", "content": "checking"}
        yield {
            "type": "tool_start", "tool_id": "tc-1",
            "tool_name": "run_shell_command",
            "display_header": "$ git status",
            "args": {"command": "git status"},
        }
        yield {
            "type": "tool_display", "tool_id": "tc-1",
            "tool_name": "run_shell_command",
            "args": {"command": "git status"}, "result": "clean",
        }
        # A start with no display_header falls back to the arguments.
        yield {
            "type": "tool_start", "tool_id": "tc-2", "tool_name": "file_read",
            "args": {"path": "app/x.py"},
        }
        yield {
            "type": "tool_display", "tool_id": "tc-2", "tool_name": "file_read",
            "args": {"path": "app/x.py"}, "result": "ERROR: missing",
            "is_error": True,
        }
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def events():
    out = []

    async def _fake_safe_push(run_id, event):
        out.append(event)

    with patch("app.streaming_tool_executor.StreamingToolExecutor", _FakeExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.task_run_stream_relay.safe_push", _fake_safe_push):
        yield out


@pytest.mark.asyncio
async def test_tool_start_is_emitted_before_the_call_returns(events):
    await task_executor.execute_task_block(_task(), run_id="run-ts-1")
    types = [e["type"] for e in events
             if e["type"] in ("task_text_delta", "task_tool_start", "task_tool_call")]
    assert types == [
        "task_text_delta", "task_tool_start", "task_tool_call",
        "task_tool_start", "task_tool_call", "task_text_delta",
    ], types
    starts = [e for e in events if e["type"] == "task_tool_start"]
    assert starts[0]["tool_id"] == "tc-1"
    assert starts[0]["tool_name"] == "run_shell_command"
    assert starts[0]["header"] == "$ git status"
    assert starts[0]["block_id"] == "task-1"
    assert starts[0]["ts"] > 0
    assert starts[1]["header"] == "app/x.py"


@pytest.mark.asyncio
async def test_tool_call_carries_header_error_flag_and_duration(events):
    await task_executor.execute_task_block(_task(), run_id="run-ts-2")
    calls = [e for e in events if e["type"] == "task_tool_call"]
    assert len(calls) == 2
    assert calls[0]["header"] == "git status"
    assert calls[0]["is_error"] is False
    assert isinstance(calls[0]["duration_ms"], int) and calls[0]["duration_ms"] >= 0
    assert calls[1]["is_error"] is True
    assert calls[1]["result_preview"] == "ERROR: missing"


def test_replay_keeps_tool_start_between_the_text_runs():
    """The relay folds only adjacent text deltas, so a reloaded view gets
    the start event in its place and the text on either side unmerged."""
    rid = "run-ts-replay"
    relay._history.pop(rid, None)
    try:
        for ev in (
            {"type": "task_text_delta", "block_id": "b", "index": None, "content": "a"},
            {"type": "task_text_delta", "block_id": "b", "index": None, "content": "b"},
            {"type": "task_tool_start", "block_id": "b", "tool_id": "t"},
            {"type": "task_tool_call", "block_id": "b", "tool_id": "t"},
            {"type": "task_text_delta", "block_id": "b", "index": None, "content": "c"},
        ):
            relay._record(rid, ev)
        got = [(e["type"], e.get("content")) for e in relay._history[rid]]
        assert got == [
            ("task_text_delta_run", "ab"),
            ("task_tool_start", None),
            ("task_tool_call", None),
            ("task_text_delta_run", "c"),
        ]
    finally:
        relay._history.pop(rid, None)
