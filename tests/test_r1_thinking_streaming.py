"""
Tests for DeepSeek R1 thinking/reasoning content streaming.

Verifies that:
1. ThinkingDelta events are wrapped in <thinking-data> tags and streamed as text
2. The closing </thinking-data> tag is emitted when text content starts
3. The closing tag is emitted at message_stop if no text content follows
4. A thinking-only response is not treated as empty (no empty heartbeat→done)
5. The thinking tag state is passed through the message_stop handler
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal mocks for the streaming loop
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Minimal provider mock that yields configurable events."""

    provider_name = "test_openai_bedrock"

    def __init__(self, events):
        self._events = events

    async def stream_response(self, *args, **kwargs):
        for ev in self._events:
            yield ev

    def build_assistant_message(self, text, tool_uses):
        return {"role": "assistant", "content": text}

    def build_tool_result_message(self, results):
        return {"role": "user", "content": "ok"}


def _make_executor(events, model_config=None):
    """Create a minimal StreamingToolExecutor wired to a fake provider."""
    from app.streaming_tool_executor import StreamingToolExecutor

    with patch.object(StreamingToolExecutor, '__init__', lambda self, **kw: None):
        exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
        exe.provider = _FakeProvider(events)
        exe.model_id = "us.deepseek.r1-v1:0"
        exe.model_config = model_config or {
            "family": "deepseek",
            "supports_thinking": True,
            "supports_assistant_prefill": False,
            "max_output_tokens": 8192,
        }
        exe.bedrock = None
        return exe


def _collect_events(events_list):
    """Collect text content from yielded events."""
    text_parts = []
    for ev in events_list:
        if ev.get("type") == "text":
            text_parts.append(ev.get("content", ""))
    return "".join(text_parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestR1ThinkingStreaming:
    """Verify thinking_delta events are streamed as <thinking-data> blocks."""

    @pytest.mark.asyncio
    async def test_thinking_then_text_wraps_in_tags(self):
        """Thinking followed by text should produce <thinking-data>...</thinking-data>."""
        from app.providers.base import ThinkingDelta, TextDelta, StreamEnd

        events = [
            ThinkingDelta(content="Let me reason about this."),
            ThinkingDelta(content=" Step 2 of reasoning."),
            TextDelta(content="The answer is 42."),
            StreamEnd(stop_reason="end_turn"),
        ]

        exe = _make_executor(events)

        # Patch tool loading to return no tools
        with patch.object(exe, '_load_and_prepare_tools',
                          return_value=([], [], set(), set(), set())):
            collected = []
            async for ev in exe.stream_with_tools(
                [{"role": "user", "content": "test"}],
                conversation_id="test-conv",
            ):
                collected.append(ev)

        full_text = _collect_events(collected)
        assert "<thinking-data>" in full_text, (
            f"Missing opening thinking tag. Got: {full_text[:200]}"
        )
        assert "</thinking-data>" in full_text, (
            f"Missing closing thinking tag. Got: {full_text[:200]}"
        )
        assert "Let me reason about this." in full_text
        assert "Step 2 of reasoning." in full_text
        assert "The answer is 42." in full_text

    @pytest.mark.asyncio
    async def test_thinking_only_response_not_empty(self):
        """When R1 emits only reasoning and no text, response should not be empty."""
        from app.providers.base import ThinkingDelta, StreamEnd

        events = [
            ThinkingDelta(content="I need to think about this carefully."),
            ThinkingDelta(content=" After consideration, the answer is clear."),
            StreamEnd(stop_reason="end_turn"),
        ]

        exe = _make_executor(events)

        with patch.object(exe, '_load_and_prepare_tools',
                          return_value=([], [], set(), set(), set())):
            collected = []
            async for ev in exe.stream_with_tools(
                [{"role": "user", "content": "test"}],
                conversation_id="test-conv-2",
            ):
                collected.append(ev)

        full_text = _collect_events(collected)
        # Must have content, not just heartbeat→done
        assert len(full_text) > 0, "Thinking-only response was empty!"
        assert "<thinking-data>" in full_text
        assert "</thinking-data>" in full_text
        assert "think about this carefully" in full_text

    @pytest.mark.asyncio
    async def test_closing_tag_before_text_content(self):
        """The </thinking-data> tag should appear before the regular text."""
        from app.providers.base import ThinkingDelta, TextDelta, StreamEnd

        events = [
            ThinkingDelta(content="Reasoning here."),
            TextDelta(content="Final answer."),
            StreamEnd(stop_reason="end_turn"),
        ]

        exe = _make_executor(events)

        with patch.object(exe, '_load_and_prepare_tools',
                          return_value=([], [], set(), set(), set())):
            collected = []
            async for ev in exe.stream_with_tools(
                [{"role": "user", "content": "test"}],
                conversation_id="test-conv-3",
            ):
                collected.append(ev)

        full_text = _collect_events(collected)
        close_pos = full_text.find("</thinking-data>")
        answer_pos = full_text.find("Final answer.")
        assert close_pos < answer_pos, (
            f"Closing tag at {close_pos} should be before answer at {answer_pos}"
        )

    @pytest.mark.asyncio
    async def test_no_thinking_no_tags(self):
        """Normal text-only response should not contain thinking tags."""
        from app.providers.base import TextDelta, StreamEnd

        events = [
            TextDelta(content="Just a normal response."),
            StreamEnd(stop_reason="end_turn"),
        ]

        exe = _make_executor(events)

        with patch.object(exe, '_load_and_prepare_tools',
                          return_value=([], [], set(), set(), set())):
            collected = []
            async for ev in exe.stream_with_tools(
                [{"role": "user", "content": "hello"}],
                conversation_id="test-conv-4",
            ):
                collected.append(ev)

        full_text = _collect_events(collected)
        assert "<thinking-data>" not in full_text
        assert "</thinking-data>" not in full_text
        assert "normal response" in full_text


class TestMessageStopThinkingTag:
    """Verify message_stop_handler closes unclosed thinking tags."""

    @pytest.mark.asyncio
    async def test_unclosed_thinking_tag_closed_at_message_stop(self):
        """If thinking tag was opened but never closed, message_stop should close it."""
        from app.message_stop_handler import handle_message_stop, MessageStopState

        state = MessageStopState(
            assistant_text="<thinking-data>Some reasoning",
            thinking_tag_opened=True,
        )
        chunk = {"type": "message_stop", "stop_reason": "end_turn"}

        executor = MagicMock()
        executor._content_optimizer = MagicMock()
        executor._content_optimizer.flush_remaining.return_value = ""
        executor._block_opening_buffer = ""
        executor._update_code_block_tracker = MagicMock()
        executor._continue_incomplete_code_block = AsyncMock()
        executor.model_config = {}

        events = []
        async for ev in handle_message_stop(
            executor=executor,
            state=state,
            chunk=chunk,
            code_block_tracker={"in_block": False, "block_type": None},
            conversation=[],
            system_content=None,
            mcp_manager=MagicMock(),
            iteration_start_time=time.time(),
            conversation_id="test",
            iteration_usage=MagicMock(),
            iteration=0,
            track_yield=lambda x: x,
        ):
            events.append(ev)

        # The closing tag should have been emitted
        text_events = [e for e in events if e.get("type") == "text"]
        all_text = "".join(e.get("content", "") for e in text_events)
        assert "</thinking-data>" in all_text, (
            f"Expected closing thinking tag in message_stop output. Got: {all_text}"
        )
        # State should be updated
        assert not state.thinking_tag_opened

    @pytest.mark.asyncio
    async def test_closed_thinking_tag_not_duplicated(self):
        """If thinking tag was already closed, message_stop should not add another."""
        from app.message_stop_handler import handle_message_stop, MessageStopState

        state = MessageStopState(
            assistant_text="<thinking-data>Reasoning</thinking-data>\n\nAnswer.",
            thinking_tag_opened=False,  # Already closed
        )
        chunk = {"type": "message_stop", "stop_reason": "end_turn"}

        executor = MagicMock()
        executor._content_optimizer = MagicMock()
        executor._content_optimizer.flush_remaining.return_value = ""
        executor._block_opening_buffer = ""
        executor._update_code_block_tracker = MagicMock()
        executor._continue_incomplete_code_block = AsyncMock()
        executor.model_config = {}

        events = []
        async for ev in handle_message_stop(
            executor=executor,
            state=state,
            chunk=chunk,
            code_block_tracker={"in_block": False, "block_type": None},
            conversation=[],
            system_content=None,
            mcp_manager=MagicMock(),
            iteration_start_time=time.time(),
            conversation_id="test",
            iteration_usage=MagicMock(),
            iteration=0,
            track_yield=lambda x: x,
        ):
            events.append(ev)

        # Should not have emitted another closing tag
        text_events = [e for e in events if e.get("type") == "text"]
        close_count = sum(
            e.get("content", "").count("</thinking-data>") for e in text_events
        )
        assert close_count == 0, (
            f"Duplicate closing tag emitted ({close_count} times)"
        )
