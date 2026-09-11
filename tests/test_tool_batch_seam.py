"""Seam test: StreamingToolExecutor actually routes a multi-tool turn through
app.tool_batch.

A unit test on tool_batch alone would pass even if the executor never called
it.  This drives ``stream_with_tools`` end to end with a fake provider that
emits two ``file_read`` calls and one ``file_write`` in a single turn, and
asserts on what the outermost surfaces show: the executor's own event stream
and the tool_result message handed to the provider.

Against the pre-batch executor (execute at content_block_stop, serially) the
overlap assertion fails; against the batched executor it passes.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

import json

from app.providers.base import StreamEnd, TextDelta, ToolUseEnd, ToolUseInput, ToolUseStart


def _tool_call(tool_id, name, args, index):
    """The three events the executor's bridge needs for one tool_use block.
    Arguments travel in ToolUseInput deltas; the bridge ignores ToolUseEnd.input."""
    return [
        ToolUseStart(id=tool_id, name=name, index=index),
        ToolUseInput(partial_json=json.dumps(args), index=index),
        ToolUseEnd(id=tool_id, name=name, input=args, index=index),
    ]


class _ScriptedProvider:
    """Replays one event sequence per model call, in order."""

    provider_name = "test_scripted"

    def __init__(self, turns: List[List[Any]]):
        self._turns = list(turns)
        self.tool_result_messages: List[List[Dict[str, Any]]] = []
        self.assistant_messages: List[Dict[str, Any]] = []

    async def stream_response(self, *a, **kw):
        events = self._turns.pop(0) if self._turns else [StreamEnd(stop_reason="end_turn")]
        for ev in events:
            yield ev

    def supports_feature(self, name):
        return False

    def build_assistant_message(self, text, tool_uses, **kw):
        msg = {"role": "assistant", "content": [{"type": "text", "text": text}] + [
            {"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["input"]}
            for tu in tool_uses]}
        self.assistant_messages.append(msg)
        return msg

    def build_tool_result_message(self, results):
        self.tool_result_messages.append(results)
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
            for r in results]}


def _make_executor(provider):
    from app.streaming_tool_executor import StreamingToolExecutor

    with patch.object(StreamingToolExecutor, "__init__", lambda self, **kw: None):
        exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
        exe.provider = provider
        exe.model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        exe.model_config = {"family": "claude", "max_output_tokens": 8192}
        exe.bedrock = None
        # Read by the usage-ledger hook at the top of each iteration.
        exe.endpoint = "bedrock"
        exe.region = "us-east-1"
        return exe


def _tool_turn():
    return [
        TextDelta(content="Reading two files, then writing one."),
        *_tool_call("t1", "file_read", {"path": "a.py"}, 1),
        *_tool_call("t2", "file_read", {"path": "b.py"}, 2),
        *_tool_call("t3", "file_write", {"path": "c.py", "content": "x"}, 3),
        StreamEnd(stop_reason="tool_use"),
    ]


def _fake_execute(log, durations):
    async def fake(ctx):
        log.append(("start", ctx.tool_id))
        yield {'type': 'tool_start', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name}
        await asyncio.sleep(durations.get(ctx.tool_id, 0.01))
        log.append(("end", ctx.tool_id))
        yield {'type': 'tool_display', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name, 'result': 'ok'}
        yield {'type': '_tool_result', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name,
               'result': f'result-{ctx.tool_id}'}
    return fake


def _patch_execute(fake):
    """Patch execute_single_tool wherever the executor may resolve it."""
    patches = [patch("app.tool_execution.execute_single_tool", fake)]
    try:
        import app.tool_batch  # noqa: F401
        patches.append(patch("app.tool_batch.execute_single_tool", fake))
    except ImportError:
        pass
    return patches


async def _run(exe, conv_id):
    events = []
    with patch.object(exe, "_load_and_prepare_tools",
                      return_value=([], [], set(), set(), set())):
        async for ev in exe.stream_with_tools(
                [{"role": "user", "content": "go"}], conversation_id=conv_id):
            events.append(ev)
    return events


@pytest.mark.timeout(60)
class TestExecutorRoutesThroughToolBatch:

    @pytest.mark.asyncio
    async def test_reads_overlap_write_waits_results_in_arrival_order(self):
        log: list = []
        provider = _ScriptedProvider([
            _tool_turn(),
            [TextDelta(content="Done."), StreamEnd(stop_reason="end_turn")],
        ])
        exe = _make_executor(provider)
        fake = _fake_execute(log, durations={"t1": 0.08, "t2": 0.01, "t3": 0.01})

        ps = _patch_execute(fake)
        for p in ps:
            p.start()
        try:
            events = await _run(exe, "seam-batch-1")
        finally:
            for p in ps:
                p.stop()

        # All three tools ran, exactly once.
        assert [t for k, t in log if k == "start"].count("t1") == 1
        assert {t for k, t in log if k == "start"} == {"t1", "t2", "t3"}

        # Reads overlapped: t2 started before the slow t1 ended.
        assert log.index(("start", "t2")) < log.index(("end", "t1")), (
            "the two file_read calls ran serially — executor is not batching")
        # Write is a barrier: did not start until both reads ended.
        assert log.index(("start", "t3")) > log.index(("end", "t1"))
        assert log.index(("start", "t3")) > log.index(("end", "t2"))

        # Outermost surface: the tool_result message handed to the provider
        # carries every id, in the model's arrival order, with the real
        # results (no stubs, no orphans).
        assert len(provider.tool_result_messages) == 1
        sent = provider.tool_result_messages[0]
        assert [r["tool_use_id"] for r in sent] == ["t1", "t2", "t3"]
        assert [r["content"] for r in sent] == ["result-t1", "result-t2", "result-t3"]

        # And the assistant message's tool_use blocks match, in order.
        tool_use_ids = [b["id"] for b in provider.assistant_messages[0]["content"]
                        if b.get("type") == "tool_use"]
        assert tool_use_ids == ["t1", "t2", "t3"]

        # Display events for all three reached the stream.
        assert {e.get("tool_id") for e in events if e.get("type") == "tool_display"} == {"t1", "t2", "t3"}

    @pytest.mark.asyncio
    async def test_single_tool_turn_still_works(self):
        """The common case must be unaffected: one call, one result."""
        log: list = []
        provider = _ScriptedProvider([
            [*_tool_call("s1", "file_read", {"path": "a.py"}, 0),
             StreamEnd(stop_reason="tool_use")],
            [TextDelta(content="Done."), StreamEnd(stop_reason="end_turn")],
        ])
        exe = _make_executor(provider)
        fake = _fake_execute(log, durations={})
        ps = _patch_execute(fake)
        for p in ps:
            p.start()
        try:
            await _run(exe, "seam-batch-2")
        finally:
            for p in ps:
                p.stop()
        assert log == [("start", "s1"), ("end", "s1")]
        assert [r["tool_use_id"] for r in provider.tool_result_messages[0]] == ["s1"]
