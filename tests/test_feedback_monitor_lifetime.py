"""The feedback-monitor task dies with the stream that started it.

An lldb dump of the frozen sweep run cli-695c6e36c9f8 showed 46 pending
``_feedback_monitor`` tasks, one per task-card iteration.  The impl's own
cancel sites sit on explicit return paths after the iteration loop; a
consumer that breaks on ``stream_end`` (the CLI, task_executor) abandons
the generator at a yield inside the loop and reaches none of them.  The
fix moves the cancel to ``stream_with_tools``'s ``finally``, which every
exit path runs, fed by a one-slot holder the impl fills.

Two hops, each asserted:
  * the wrapper cancels whatever lands in the holder, including when the
    consumer breaks early;
  * the real impl puts its monitor task in the holder.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.streaming_tool_executor import StreamingToolExecutor


def _bare_executor():
    ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex.provider = None
    ex.model_config = {}
    ex.model_id = "m"
    ex.endpoint = "bedrock"
    return ex


class TestWrapperCancelsMonitor:
    @pytest.mark.asyncio
    async def test_consumer_breaking_on_stream_end_cancels_monitor(self):
        """Break on stream_end under ``aclosing`` -- what task_executor does
        -- and the monitor is cancelled synchronously on the way out."""
        from contextlib import aclosing
        ex = _bare_executor()
        started = {}

        async def _never():
            await asyncio.Event().wait()

        async def fake_impl(self, messages, _monitor_holder=None, **kw):
            started["task"] = asyncio.create_task(_never())
            _monitor_holder[0] = started["task"]
            yield {"type": "text", "content": "x"}
            yield {"type": "stream_end"}
            yield {"type": "text", "content": "never consumed"}

        with patch.object(StreamingToolExecutor, "_stream_with_tools_impl", fake_impl):
            async with aclosing(ex.stream_with_tools([], conversation_id="c1")) as s:
                async for chunk in s:
                    if chunk["type"] == "stream_end":
                        break
        # No sleep: Task.cancel() ran inside aclose(), before we got here.
        assert started["task"].cancelling() or started["task"].cancelled(), (
            "monitor task survived the consumer abandoning the stream")

    @pytest.mark.asyncio
    async def test_task_executor_closes_the_stream_it_breaks_out_of(self):
        """The seam: execute_task_block breaks on stream_end; it must aclose
        the generator so the wrapper's finally runs at once, not at GC."""
        from app.agents import task_executor
        from app.models.task_card import Block
        closed = {"n": 0, "task": None}

        class _Exec:
            def __init__(self, *a, **kw):
                pass

            async def stream_with_tools(self, messages, tools=None, **kwargs):
                try:
                    yield {"type": "text", "content": "done"}
                    yield {"type": "stream_end"}
                    yield {"type": "text", "content": "after end"}
                finally:
                    closed["n"] += 1
                    # Who closed us?  Under aclosing it is the caller's own
                    # task.  An abandoned generator is instead closed by the
                    # asyncgen GC hook in a SEPARATE task, later -- which the
                    # executor's own awaits give time to run, so a bare
                    # "closed by return" check cannot tell the two apart.
                    closed["task"] = asyncio.current_task()

        block = Block(block_type="task", id="t1", name="T", instructions="do")
        with patch("app.streaming_tool_executor.StreamingToolExecutor", _Exec), \
             patch("app.agents.models.ModelManager.get_state",
                   return_value={"aws_region": "us-east-1", "aws_profile": "x"}), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
            await task_executor.execute_task_block(block, run_id="r1")
        assert closed["n"] == 1
        assert closed["task"] is asyncio.current_task(), (
            "stream was closed by the GC finalizer, not by execute_task_block")

    @pytest.mark.asyncio
    async def test_monitor_cancelled_on_normal_exhaustion_too(self):
        ex = _bare_executor()
        holder = {}

        async def _never():
            await asyncio.Event().wait()

        async def fake_impl(self, messages, _monitor_holder=None, **kw):
            holder["task"] = asyncio.create_task(_never())
            _monitor_holder[0] = holder["task"]
            yield {"type": "stream_end"}

        with patch.object(StreamingToolExecutor, "_stream_with_tools_impl", fake_impl):
            async for _ in ex.stream_with_tools([], conversation_id="c2"):
                pass
        await asyncio.sleep(0)
        assert holder["task"].cancelled()


class TestImplFillsHolder:
    @pytest.mark.asyncio
    async def test_real_impl_hands_monitor_to_wrapper(self):
        """Drive the real impl to its provider-None guard, which is the
        first exit after the monitor is created, and read the holder."""
        ex = _bare_executor()
        holder = [None]

        async def _tools(self, *a, **k):
            return [], [], set(), set(), set()

        mgr = SimpleNamespace(reset_turn_tool_count=lambda c: None, is_initialized=True)
        with patch.object(StreamingToolExecutor, "_load_and_prepare_tools", _tools), \
             patch.object(StreamingToolExecutor, "_build_conversation_from_messages",
                          lambda self, m: ([], None)), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mgr):
            chunks = [c async for c in ex._stream_with_tools_impl(
                [], conversation_id="c3", _monitor_holder=holder)]
        assert any(c.get("type") == "stream_end" for c in chunks)
        assert isinstance(holder[0], asyncio.Task), holder
