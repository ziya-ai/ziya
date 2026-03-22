"""
Tests for _simple_invoke cancellation support (Bug #6).

Verifies that Ctrl+C (cancellation) is respected in both the streaming
and non-streaming paths of CLI._simple_invoke, preventing the CLI from
hanging indefinitely when the user interrupts a long-running LLM call.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def cli_instance():
    """Create a minimal CLI instance with mocked model."""
    # Patch heavy dependencies that CLI.__init__ touches
    with patch('app.cli.PromptSession'), \
         patch('app.cli.FileHistory'), \
         patch('app.cli.PathCompleter'), \
         patch('app.cli.WordCompleter'):
        from app.cli import CLI
        cli = CLI(files=[])
        cli._model = MagicMock()
        return cli


class TestSimpleInvokeStreamingCancellation:
    """Streaming path should stop yielding chunks when cancellation is requested."""

    @pytest.mark.asyncio
    async def test_cancellation_stops_streaming(self, cli_instance):
        """When _cancellation_requested is set, streaming should stop and return partial content."""
        chunks_yielded = 0

        async def fake_astream(messages, **kwargs):
            nonlocal chunks_yielded
            for text in ["Hello ", "world ", "this ", "should ", "not ", "appear"]:
                chunks_yielded += 1
                # Set cancellation flag after yielding chunk 2.
                # The flag is checked at the TOP of the loop, so chunk 2
                # is yielded but the loop breaks before processing chunk 3.
                if chunks_yielded == 2:
                    cli_instance._cancellation_requested = True
                yield MagicMock(content=text)

        cli_instance._model.astream = fake_astream

        result = await cli_instance._simple_invoke([], stream=True)

        # Chunk 1 ("Hello ") is processed before the flag is set
        assert "Hello " in result
        # Chunk 2 ("world ") sets the flag, but the check happens on the
        # NEXT iteration, so "world " may or may not be included depending
        # on generator scheduling. The key invariant is that later chunks
        # are NOT included.
        assert "should " not in result
        assert "not " not in result
        assert "appear" not in result

    @pytest.mark.asyncio
    async def test_streaming_returns_accumulated_on_cancel(self, cli_instance):
        """Partial content accumulated before cancellation should be returned."""
        async def fake_astream(messages, **kwargs):
            yield MagicMock(content="partial ")
            cli_instance._cancellation_requested = True
            yield MagicMock(content="ignored")

        cli_instance._model.astream = fake_astream

        result = await cli_instance._simple_invoke([], stream=True)
        assert result == "partial "

    @pytest.mark.asyncio
    async def test_streaming_no_cancellation_returns_full(self, cli_instance):
        """Without cancellation, all chunks should be returned."""
        async def fake_astream(messages, **kwargs):
            for text in ["A", "B", "C"]:
                yield MagicMock(content=text)

        cli_instance._model.astream = fake_astream

        result = await cli_instance._simple_invoke([], stream=True)
        assert result == "ABC"


class TestSimpleInvokeNonStreamingCancellation:
    """Non-streaming path should cancel the ainvoke task when cancellation is requested."""

    @pytest.mark.asyncio
    async def test_cancellation_aborts_ainvoke(self, cli_instance):
        """When _cancellation_requested is set during ainvoke, it should return empty string."""
        async def slow_ainvoke(messages, **kwargs):
            # Simulate a slow LLM call (5 seconds)
            await asyncio.sleep(5)
            return MagicMock(content="should not reach here")

        cli_instance._model.ainvoke = slow_ainvoke

        # Set cancellation after a short delay
        async def cancel_after_delay():
            await asyncio.sleep(0.3)
            cli_instance._cancellation_requested = True

        asyncio.create_task(cancel_after_delay())

        result = await cli_instance._simple_invoke([], stream=False)

        # Should have returned empty due to cancellation, not waited 5 seconds
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_cancellation_returns_result(self, cli_instance):
        """Without cancellation, ainvoke result should be returned normally."""
        async def fast_ainvoke(messages, **kwargs):
            await asyncio.sleep(0.05)
            return MagicMock(content="complete response")

        cli_instance._model.ainvoke = fast_ainvoke

        result = await cli_instance._simple_invoke([], stream=False)
        assert result == "complete response"

    @pytest.mark.asyncio
    async def test_ainvoke_dict_result_handled(self, cli_instance):
        """Dict-style results from ainvoke should be handled correctly."""
        async def dict_ainvoke(messages, **kwargs):
            return {"content": "dict response"}

        cli_instance._model.ainvoke = dict_ainvoke

        result = await cli_instance._simple_invoke([], stream=False)
        assert result == "dict response"

    @pytest.mark.asyncio
    async def test_cancellation_timing_is_responsive(self, cli_instance):
        """Cancellation should be detected within ~200ms (the poll interval)."""
        import time

        async def very_slow_ainvoke(messages, **kwargs):
            await asyncio.sleep(30)
            return MagicMock(content="too slow")

        cli_instance._model.ainvoke = very_slow_ainvoke

        async def cancel_soon():
            await asyncio.sleep(0.1)
            cli_instance._cancellation_requested = True

        asyncio.create_task(cancel_soon())

        start = time.monotonic()
        result = await cli_instance._simple_invoke([], stream=False)
        elapsed = time.monotonic() - start

        assert result == ""
        # Should complete in well under 1 second (poll interval is 200ms)
        assert elapsed < 1.0, f"Cancellation took {elapsed:.2f}s, expected < 1.0s"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
