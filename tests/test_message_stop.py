"""Tests for app.message_stop_handler — extracted Phase 5d."""
import asyncio
import pytest
import time
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass


@dataclass
class FakeIterationUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _make_executor():
    """Create a minimal mock executor with the methods message_stop_handler needs."""
    ex = MagicMock()
    ex._block_opening_buffer = ""
    ex._content_optimizer = MagicMock()
    ex._content_optimizer.flush_remaining.return_value = ""
    ex._update_code_block_tracker = MagicMock()
    ex._continue_incomplete_code_block = AsyncMock(return_value=AsyncIterHelper([]))
    return ex


class AsyncIterHelper:
    """Wrap a list into an async iterator for mocking async-for."""
    def __init__(self, items):
        self._items = items
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


def _identity(x):
    """Stand-in for track_yield — returns input unchanged."""
    return x


async def _collect(gen):
    """Collect all items from an async generator."""
    items = []
    async for item in gen:
        items.append(item)
    return items


class TestFlushBuffers:
    """Verify that all buffer types are flushed on message_stop."""

    @pytest.mark.asyncio
    async def test_flushes_block_opening_buffer(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        ex._block_opening_buffer = "```python\n"
        state = MessageStopState(assistant_text="some text")
        tracker = {'in_block': False, 'block_type': None}

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id="test", iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        text_events = [e for e in events if isinstance(e, dict) and e.get('type') == 'text']
        assert any("```python" in e['content'] for e in text_events)
        assert state.assistant_text.startswith("some text```python")
        assert ex._block_opening_buffer == ""

    @pytest.mark.asyncio
    async def test_flushes_viz_buffer(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState(assistant_text="", viz_buffer="```mermaid\ngraph LR\n```")
        tracker = {'in_block': False, 'block_type': None}

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        text_events = [e for e in events if isinstance(e, dict) and e.get('type') == 'text']
        assert any("mermaid" in e['content'] for e in text_events)

    @pytest.mark.asyncio
    async def test_flushes_content_optimizer(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        ex._content_optimizer.flush_remaining.return_value = "remaining text"
        state = MessageStopState()
        tracker = {'in_block': False, 'block_type': None}

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        text_events = [e for e in events if isinstance(e, dict) and e.get('type') == 'text']
        assert any(e['content'] == "remaining text" for e in text_events)

    @pytest.mark.asyncio
    async def test_flushes_content_buffer(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState(content_buffer="buffered content\n")
        tracker = {'in_block': False, 'block_type': None}

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        text_events = [e for e in events if isinstance(e, dict) and e.get('type') == 'text']
        assert any("buffered content" in e['content'] for e in text_events)


class TestStopReason:

    @pytest.mark.asyncio
    async def test_sets_last_stop_reason(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState()
        tracker = {'in_block': False, 'block_type': None}

        await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'max_tokens'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        assert state.last_stop_reason == 'max_tokens'

    @pytest.mark.asyncio
    async def test_defaults_to_end_turn(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState()
        tracker = {'in_block': False, 'block_type': None}

        await _collect(handle_message_stop(
            executor=ex, state=state, chunk={},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        assert state.last_stop_reason == 'end_turn'


class TestUsageRecording:

    @pytest.mark.asyncio
    async def test_records_usage_when_tokens_present(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState()
        tracker = {'in_block': False, 'block_type': None}
        usage = FakeIterationUsage(input_tokens=1000, cache_read_tokens=500)

        with patch('app.streaming_tool_executor.get_global_usage_tracker') as mock_get:
            mock_tracker = MagicMock()
            mock_get.return_value = mock_tracker

            await _collect(handle_message_stop(
                executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
                code_block_tracker=tracker, conversation=[], system_content=None,
                mcp_manager=None, iteration_start_time=time.time(),
                conversation_id="conv-123", iteration_usage=usage,
                iteration=0, track_yield=_identity,
            ))

            mock_tracker.record_usage.assert_called_once_with("conv-123", usage)

    @pytest.mark.asyncio
    async def test_no_usage_recording_without_tokens(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState()
        tracker = {'in_block': False, 'block_type': None}
        usage = FakeIterationUsage(input_tokens=0)

        with patch('app.streaming_tool_executor.get_global_usage_tracker') as mock_get:
            await _collect(handle_message_stop(
                executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
                code_block_tracker=tracker, conversation=[], system_content=None,
                mcp_manager=None, iteration_start_time=time.time(),
                conversation_id="conv-123", iteration_usage=usage,
                iteration=0, track_yield=_identity,
            ))

            mock_get.assert_not_called()


class TestContinuation:

    @pytest.mark.asyncio
    async def test_no_continuation_when_block_closed(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        state = MessageStopState()
        tracker = {'in_block': False, 'block_type': None}

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        assert state.continuation_happened is False
        assert not any(e.get('rewind') for e in events if isinstance(e, dict))

    @pytest.mark.asyncio
    async def test_continuation_sets_flag(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        # Simulate: continuation produces content that closes the block
        async def fake_continue(*args, **kwargs):
            yield {'type': 'text', 'content': '```\n'}

        ex._continue_incomplete_code_block = fake_continue
        state = MessageStopState(assistant_text="```python\nprint('hi')\n")
        # Start with block open; the fake continuation closes it
        tracker = {'in_block': True, 'block_type': 'python', 'backtick_count': 3}
        # Mock _update_code_block_tracker to close the block when ``` is seen
        call_count = [0]
        def close_tracker(text, trk):
            if '```' in text and trk.get('in_block'):
                trk['in_block'] = False
                trk['block_type'] = None
        ex._update_code_block_tracker.side_effect = close_tracker

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        assert state.continuation_happened is True
        # Should have yielded a rewind event
        rewind_events = [e for e in events if isinstance(e, dict) and e.get('rewind')]
        assert len(rewind_events) >= 1

    @pytest.mark.asyncio
    async def test_continuation_failure_yields_marker(self):
        from app.message_stop_handler import handle_message_stop, MessageStopState

        ex = _make_executor()
        async def failing_continue(*args, **kwargs):
            raise RuntimeError("ThrottlingException simulated")
            yield  # make it a generator

        ex._continue_incomplete_code_block = failing_continue
        state = MessageStopState(assistant_text="```diff\n-old\n")
        tracker = {'in_block': True, 'block_type': 'diff', 'backtick_count': 3}

        events = await _collect(handle_message_stop(
            executor=ex, state=state, chunk={'stop_reason': 'end_turn'},
            code_block_tracker=tracker, conversation=[], system_content=None,
            mcp_manager=None, iteration_start_time=time.time(),
            conversation_id=None, iteration_usage=FakeIterationUsage(),
            iteration=0, track_yield=_identity,
        ))

        failure_events = [e for e in events if isinstance(e, dict) and e.get('type') == 'continuation_failed']
        assert len(failure_events) == 1
        assert failure_events[0]['can_retry'] is True
