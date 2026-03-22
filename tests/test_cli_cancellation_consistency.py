"""
Tests for consistent CancelledError handling across all CLI execution paths.

Issue #7: CancelledError Logic Inconsistency Between Providers
- Bedrock path propagated CancelledError to ask(), which handled it correctly.
- Non-Bedrock path swallowed CancelledError in _stream_handler, returning
  partial content as if the request succeeded — so ask() never knew cancellation
  happened and added incomplete responses to history without the "[cancelled]" marker.
- _simple_invoke had two sub-paths: streaming silently returned partial content,
  non-streaming returned empty string. Neither re-raised.

After fix: all paths set self._partial_response and re-raise CancelledError,
so ask() always handles cancellation uniformly.
"""

import asyncio
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is on sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture
def cli_instance():
    """Create a minimal CLI instance for testing cancellation behavior."""
    # Patch out prompt_toolkit's PromptSession which needs a real terminal
    with patch('app.cli.PromptSession'), \
         patch('app.cli.FileHistory'):
        from app.cli import CLI
        cli = CLI(files=[])
        cli._model = MagicMock()
        cli._init_error = None
        return cli


class TestStreamHandlerReRaises:
    """_stream_handler must re-raise CancelledError after preserving partial content."""

    @pytest.mark.asyncio
    async def test_stream_handler_reraises_cancelled_error(self, cli_instance):
        """_stream_handler should set _partial_response and re-raise CancelledError."""
        async def cancelling_generator():
            yield {'type': 'text', 'content': 'partial '}
            yield {'type': 'text', 'content': 'content'}
            raise asyncio.CancelledError("cancelled")

        with pytest.raises(asyncio.CancelledError):
            await cli_instance._stream_handler(cancelling_generator(), stream=True)

        # Partial content should be preserved
        assert cli_instance._partial_response == 'partial content'

    @pytest.mark.asyncio
    async def test_stream_handler_returns_full_on_success(self, cli_instance):
        """When not cancelled, _stream_handler should return the full response."""
        async def normal_generator():
            yield {'type': 'text', 'content': 'hello '}
            yield {'type': 'text', 'content': 'world'}
            yield {'type': 'stream_end'}

        result = await cli_instance._stream_handler(normal_generator(), stream=True)
        assert result == 'hello world'


class TestSimpleInvokeStreamingCancellation:
    """_simple_invoke streaming path must re-raise CancelledError."""

    @pytest.mark.asyncio
    async def test_simple_invoke_streaming_reraises(self, cli_instance):
        """Streaming _simple_invoke should preserve partial and re-raise."""
        chunks_yielded = 0

        async def mock_astream(messages, **kwargs):
            nonlocal chunks_yielded
            for text in ['chunk1 ', 'chunk2 ']:
                chunks_yielded += 1
                yield MagicMock(content=text)
            raise asyncio.CancelledError("cancelled")

        cli_instance._model.astream = mock_astream

        with pytest.raises(asyncio.CancelledError):
            await cli_instance._simple_invoke([], stream=True)

        assert cli_instance._partial_response == 'chunk1 chunk2 '

    @pytest.mark.asyncio
    async def test_simple_invoke_streaming_returns_on_success(self, cli_instance):
        """Streaming _simple_invoke should return full response normally."""
        async def mock_astream(messages, **kwargs):
            for text in ['hello ', 'world']:
                yield MagicMock(content=text)

        cli_instance._model.astream = mock_astream

        result = await cli_instance._simple_invoke([], stream=True)
        assert result == 'hello world'


class TestSimpleInvokeNonStreamingCancellation:
    """_simple_invoke non-streaming path must re-raise CancelledError."""

    @pytest.mark.asyncio
    async def test_simple_invoke_nonstreaming_reraises_on_cancel_request(self, cli_instance):
        """Non-streaming _simple_invoke should raise CancelledError when cancellation requested."""
        # Make ainvoke take a long time so the polling loop runs
        async def slow_ainvoke(messages, **kwargs):
            await asyncio.sleep(10)
            return MagicMock(content='result')

        cli_instance._model.ainvoke = slow_ainvoke
        # Request cancellation after a short delay
        cli_instance._cancellation_requested = True

        with pytest.raises(asyncio.CancelledError):
            await cli_instance._simple_invoke([], stream=False)

        assert cli_instance._partial_response == ""


class TestAskUnifiedCancellation:
    """ask() should handle CancelledError the same regardless of provider."""

    @pytest.mark.asyncio
    async def test_ask_preserves_partial_on_cancellation(self, cli_instance):
        """ask() should return partial content and add to history on cancellation."""
        cli_instance._partial_response = ""

        async def mock_run(*args, **kwargs):
            cli_instance._partial_response = "partial answer"
            raise asyncio.CancelledError("cancelled")

        cli_instance._run_with_tools_and_validate = mock_run

        result = await cli_instance.ask("test question")

        # Should return the partial response
        assert result == "partial answer"

        # Should add both human and (partial) AI messages to history
        assert len(cli_instance.history) == 2
        assert cli_instance.history[0] == {'type': 'human', 'content': 'test question'}
        assert cli_instance.history[1] == {'type': 'ai', 'content': 'partial answer'}

    @pytest.mark.asyncio
    async def test_ask_handles_cancellation_with_no_partial(self, cli_instance):
        """ask() should handle cancellation even when no content was generated."""
        cli_instance._partial_response = ""

        async def mock_run(*args, **kwargs):
            raise asyncio.CancelledError("cancelled immediately")

        cli_instance._run_with_tools_and_validate = mock_run

        result = await cli_instance.ask("test question")

        # Should return empty string
        assert result == ""

        # Should add human message but not empty AI message
        assert len(cli_instance.history) == 1
        assert cli_instance.history[0] == {'type': 'human', 'content': 'test question'}


class TestNonBedrockPathCancellation:
    """Non-Bedrock path in _run_with_tools_from_messages must propagate CancelledError."""

    @pytest.mark.asyncio
    async def test_non_bedrock_path_propagates_cancellation(self, cli_instance):
        """Google/OpenAI/Anthropic path should re-raise CancelledError from task."""
        async def cancelling_stream(messages, **kwargs):
            yield {'type': 'text', 'content': 'partial'}
            raise asyncio.CancelledError("cancelled")

        cli_instance._model.astream = cancelling_stream

        with patch.dict(os.environ, {'ZIYA_ENDPOINT': 'google'}), \
             patch('app.mcp.manager.get_mcp_manager') as mock_mgr, \
             patch('app.mcp.enhanced_tools.create_secure_mcp_tools', return_value=[MagicMock()]):
            mock_mgr.return_value = MagicMock(is_initialized=True)

            with pytest.raises(asyncio.CancelledError):
                await cli_instance._run_with_tools_from_messages([], stream=True)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
