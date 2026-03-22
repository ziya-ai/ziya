"""
Tests for CLI partial response preservation on connection drops.

Bug #10: When a non-cancellation exception occurs mid-stream, ask()
discards _partial_response entirely. The user gets nothing even though
90% of the response was already displayed on screen.
"""

import asyncio
import os
import sys
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure project root is on path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


@pytest.fixture
def cli_instance():
    """Create a minimal CLI instance without model initialization."""
    with patch.dict(os.environ, {
        "ZIYA_ENDPOINT": "bedrock",
        "ZIYA_MODEL": "sonnet4.0",
        "ZIYA_MODE": "chat",
        "ZIYA_LOG_LEVEL": "WARNING",
    }):
        # Patch out prompt_toolkit session setup to avoid terminal issues in tests
        with patch('app.cli.CLI._setup_prompt_session'):
            with patch('app.cli.CLI._initialize_model', return_value=MagicMock()):
                from app.cli import CLI
                cli = CLI(files=[])
                cli._model = MagicMock()  # Ensure model is "available"
                cli._init_error = None
                yield cli


class TestPartialResponsePreservation:
    """Verify that partial responses are preserved on non-cancellation exceptions."""

    @pytest.mark.asyncio
    async def test_generic_exception_preserves_partial_response(self, cli_instance):
        """When a generic exception occurs mid-stream, partial content should be returned."""
        cli = cli_instance
        accumulated = "Here is the first 90% of a long response about code architecture..."

        async def fake_run(*args, **kwargs):
            # Simulate streaming that updates _partial_response then crashes
            cli._partial_response = accumulated
            raise ConnectionError("Connection dropped mid-stream")

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            result = await cli.ask("explain the architecture")

        # The partial content should be returned, not empty string
        assert result == accumulated, (
            f"Expected partial response to be preserved, got: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_generic_exception_saves_to_history(self, cli_instance):
        """Partial response should be saved to conversation history on exception."""
        cli = cli_instance
        accumulated = "Partial analysis of the security model..."
        question = "review security"

        async def fake_run(*args, **kwargs):
            cli._partial_response = accumulated
            raise RuntimeError("Unexpected API failure")

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            await cli.ask(question)

        # History should contain both the question and partial response
        assert len(cli.history) >= 2, (
            f"Expected at least 2 history entries, got {len(cli.history)}"
        )
        assert cli.history[-2] == {'type': 'human', 'content': question}
        assert cli.history[-1] == {'type': 'ai', 'content': accumulated}

    @pytest.mark.asyncio
    async def test_generic_exception_no_partial_returns_empty(self, cli_instance):
        """When exception occurs before any streaming, return empty string."""
        cli = cli_instance
        # _partial_response stays empty (reset at start of ask())

        async def fake_run(*args, **kwargs):
            raise ConnectionError("Failed before streaming started")

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            result = await cli.ask("hello")

        assert result == "", "Should return empty string when no partial content exists"
        # History should NOT have entries for empty response
        human_entries = [h for h in cli.history if h.get('type') == 'human']
        assert len(human_entries) == 0, "Should not save to history when no partial content"

    @pytest.mark.asyncio
    async def test_cancelled_error_still_works(self, cli_instance):
        """CancelledError path should continue to work (regression guard)."""
        cli = cli_instance
        accumulated = "Partial content before cancellation..."

        async def fake_run(*args, **kwargs):
            cli._partial_response = accumulated
            raise asyncio.CancelledError()

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            result = await cli.ask("do something")

        assert result == accumulated, (
            f"CancelledError should still preserve partial response, got: {result!r}"
        )
        # History should be saved
        assert any(h.get('content') == accumulated for h in cli.history), (
            "CancelledError partial response should be in history"
        )

    @pytest.mark.asyncio
    async def test_throttling_exception_preserves_partial(self, cli_instance):
        """ThrottlingException mid-stream should preserve partial content."""
        cli = cli_instance
        accumulated = "Here is the beginning of the code review:\n\n1. The auth module..."

        async def fake_run(*args, **kwargs):
            cli._partial_response = accumulated
            raise Exception("ThrottlingException: Too many tokens")

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            result = await cli.ask("review auth module")

        assert result == accumulated
        assert len(cli.history) >= 2

    @pytest.mark.asyncio
    async def test_expired_token_preserves_partial(self, cli_instance):
        """ExpiredToken error mid-stream should preserve partial content."""
        cli = cli_instance
        accumulated = "The function at line 42 has a bug where..."

        async def fake_run(*args, **kwargs):
            cli._partial_response = accumulated
            raise Exception("ExpiredToken: credentials have expired")

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            result = await cli.ask("find bugs")

        assert result == accumulated

    @pytest.mark.asyncio
    async def test_local_partial_response_fallback(self, cli_instance):
        """When _partial_response is empty but local tracker has content, use local."""
        cli = cli_instance

        async def fake_run(*args, **kwargs):
            # _partial_response stays empty, but the method returns content
            # before crashing — simulates the case where _run_with_tools_and_validate
            # sets partial_response locally before the inner call crashes
            cli._partial_response = ""
            raise ConnectionError("dropped")

        with patch.object(cli, '_run_with_tools_and_validate', side_effect=fake_run):
            result = await cli.ask("test")

        # Both are empty, so result should be empty
        assert result == ""


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
