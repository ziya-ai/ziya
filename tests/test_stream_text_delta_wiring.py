"""
Integration test for the text_delta wiring in stream_with_tools().

This tests the *call site* where stream_with_tools() extracts text from
a TextDelta stream event and passes it to process_text_delta().  The unit
tests in test_text_delta_processor.py cover the helper in isolation, but
they cannot catch wiring bugs like a missing `text = delta.get('text', '')`
at the call site — which is exactly the bug that shipped in Phase 5c.

The test mocks the provider layer to emit controlled stream events and
asserts that text arrives in the yielded output events.
"""

import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

from app.providers.base import TextDelta, StreamEnd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor():
    """Build a StreamingToolExecutor with __init__ bypassed and
    enough state wired up for stream_with_tools to run the text_delta path."""
    with patch.dict(os.environ, {
        "ZIYA_ENDPOINT": "bedrock",
        "ZIYA_MODEL": "sonnet3.7",
    }):
        with patch(
            'app.streaming_tool_executor.StreamingToolExecutor.__init__',
            return_value=None,
        ):
            from app.streaming_tool_executor import StreamingToolExecutor
            executor = StreamingToolExecutor.__new__(StreamingToolExecutor)

            executor.model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            executor.model_config = {
                "family": "claude",
                "max_output_tokens": 8192,
                "supports_assistant_prefill": True,
            }
            executor.bedrock = None

            # Provider mock — stream_response is async generator
            provider = MagicMock()
            provider.provider_name = "mock"
            executor.provider = provider

            # Attributes that stream_with_tools expects to exist
            executor._block_opening_buffer = ""
            executor._normalize_fence_spacing = lambda text, tracker: text
            executor._update_code_block_tracker = lambda text, tracker: None

            # Content optimizer
            optimizer = MagicMock()
            optimizer.add_content.side_effect = lambda t: [t] if t else []
            optimizer.flush_remaining.return_value = ""
            executor._content_optimizer = optimizer

            # Methods called in the setup phase of stream_with_tools
            executor._build_conversation_from_messages = MagicMock(
                return_value=(
                    [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
                    "system prompt",
                )
            )
            executor._format_tools_for_api = MagicMock(return_value=[])
            executor._build_provider_config = MagicMock(
                return_value=MagicMock(max_output_tokens=8192)
            )
            executor._handle_usage_event = MagicMock()
            executor._build_tool_reminder_message = MagicMock(
                return_value={"role": "user", "content": "reminder"}
            )

            return executor


async def _collect_events(async_gen, max_events=100):
    """Drain an async generator into a list, with a safety cap."""
    events = []
    async for event in async_gen:
        events.append(event)
        if len(events) >= max_events:
            break
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTextDeltaWiring:
    """Verify that text_delta events flow through stream_with_tools correctly.

    These tests would have caught the Phase 5c regression where
    `text = delta.get('text', '')` was missing at the call site.
    """

    @pytest.mark.asyncio
    async def test_text_delta_yields_text_event(self):
        """A TextDelta from the provider should produce a text event in output."""
        executor = _make_executor()

        # Provider yields one text chunk then stops
        async def mock_stream(*args, **kwargs):
            yield TextDelta(content="Hello from the model")
            yield StreamEnd(stop_reason="end_turn")

        executor.provider.stream_response = mock_stream

        events = await _collect_events(
            executor.stream_with_tools(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
            )
        )

        text_events = [e for e in events if e.get('type') == 'text']
        combined_text = ''.join(e.get('content', '') for e in text_events)
        assert "Hello from the model" in combined_text, (
            f"Expected 'Hello from the model' in text events, got: {text_events}"
        )

    @pytest.mark.asyncio
    async def test_multiple_text_deltas_accumulate(self):
        """Multiple TextDelta events should all appear in output."""
        executor = _make_executor()

        async def mock_stream(*args, **kwargs):
            yield TextDelta(content="First chunk. ")
            yield TextDelta(content="Second chunk.")
            yield StreamEnd(stop_reason="end_turn")

        executor.provider.stream_response = mock_stream

        events = await _collect_events(
            executor.stream_with_tools(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
            )
        )

        text_events = [e for e in events if e.get('type') == 'text']
        combined_text = ''.join(e.get('content', '') for e in text_events)
        assert "First chunk" in combined_text
        assert "Second chunk" in combined_text

    @pytest.mark.asyncio
    async def test_empty_text_delta_no_crash(self):
        """A TextDelta with empty content should not crash."""
        executor = _make_executor()

        async def mock_stream(*args, **kwargs):
            yield TextDelta(content="")
            yield TextDelta(content="After empty.")
            yield StreamEnd(stop_reason="end_turn")

        executor.provider.stream_response = mock_stream

        events = await _collect_events(
            executor.stream_with_tools(
                messages=[{"role": "user", "content": "Hi"}],
                tools=[],
            )
        )

        # Should not raise; "After empty." should appear
        text_events = [e for e in events if e.get('type') == 'text']
        combined_text = ''.join(e.get('content', '') for e in text_events)
        assert "After empty." in combined_text
