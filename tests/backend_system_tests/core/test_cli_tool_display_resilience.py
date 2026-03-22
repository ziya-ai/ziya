"""
Tests for CLI tool_display handler resilience.

Verifies that malformed or unexpected chunk data in the tool_display handler
does not crash the streaming session, but instead degrades gracefully.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock


def _make_cli():
    """Create a CLI instance with a stubbed model to avoid real initialization."""
    with patch('app.cli.CLI._initialize_model', return_value=MagicMock()):
        from app.cli import CLI
        cli = CLI(files=[])
    return cli


def _run_stream(cli, chunks):
    """Feed chunks through _stream_handler and collect the result."""

    async def _gen():
        for c in chunks:
            yield c

    async def _run():
        return await cli._stream_handler(_gen(), stream=False)

    return asyncio.get_event_loop().run_until_complete(_run())


class TestToolDisplayResilience:
    """Malformed tool_display chunks must not crash the stream."""

    def test_none_result(self, capsys):
        """result=None should not raise AttributeError on .startswith()."""
        cli = _make_cli()
        chunks = [
            {'type': 'tool_display', 'tool_name': 'mcp_test', 'result': None, 'args': {}},
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        # Should complete without raising
        assert isinstance(result, str)
        captured = capsys.readouterr()
        # Should print the tool box (header + footer) without crashing
        assert '┌─' in captured.out or '⚠' in captured.out

    def test_args_is_none(self, capsys):
        """args=None should not raise AttributeError on .get()."""
        cli = _make_cli()
        chunks = [
            {'type': 'tool_display', 'tool_name': 'mcp_test', 'result': 'ok', 'args': None},
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        assert isinstance(result, str)
        captured = capsys.readouterr()
        assert '┌─' in captured.out or '⚠' in captured.out

    def test_args_is_string(self, capsys):
        """args as a string instead of dict should not crash."""
        cli = _make_cli()
        chunks = [
            {'type': 'tool_display', 'tool_name': 'mcp_test', 'result': 'ok', 'args': 'not-a-dict'},
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        assert isinstance(result, str)

    def test_result_is_integer(self, capsys):
        """result as a non-string type should not crash."""
        cli = _make_cli()
        chunks = [
            {'type': 'tool_display', 'tool_name': 'mcp_test', 'result': 42, 'args': {}},
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        assert isinstance(result, str)

    def test_missing_all_fields(self, capsys):
        """A tool_display chunk with no fields at all should degrade gracefully."""
        cli = _make_cli()
        chunks = [
            {'type': 'tool_display'},
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        assert isinstance(result, str)

    def test_thought_is_non_string(self, capsys):
        """thought as a non-string (e.g. dict) in args should not crash."""
        cli = _make_cli()
        chunks = [
            {
                'type': 'tool_display',
                'tool_name': 'mcp_sequentialthinking',
                'result': '',
                'args': {'thought': {'nested': 'object'}, 'thoughtNumber': 1, 'totalThoughts': 3},
            },
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        assert isinstance(result, str)

    def test_search_args_parses_to_list(self, capsys):
        """tool_input that parses to a JSON list instead of dict should not crash."""
        cli = _make_cli()
        chunks = [
            {
                'type': 'tool_display',
                'tool_name': 'mcp_WorkspaceSearch',
                'result': 'found stuff',
                'args': {'tool_input': '["not", "a", "dict"]'},
            },
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        assert isinstance(result, str)

    def test_normal_chunk_still_renders(self, capsys):
        """A well-formed tool_display chunk should still render correctly."""
        cli = _make_cli()
        chunks = [
            {
                'type': 'tool_display',
                'tool_name': 'mcp_run_shell_command',
                'result': 'hello world',
                'args': {'command': 'echo hello'},
            },
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        captured = capsys.readouterr()
        assert '┌─' in captured.out
        assert 'hello world' in captured.out
        assert '└─' in captured.out

    def test_malformed_chunk_does_not_stop_stream(self, capsys):
        """A malformed chunk followed by valid text should not lose the text."""
        cli = _make_cli()
        chunks = [
            {'type': 'tool_display', 'tool_name': None, 'result': None, 'args': 123},
            {'type': 'text', 'content': 'important answer'},
            {'type': 'stream_end'},
        ]
        result = _run_stream(cli, chunks)
        # The text chunk after the malformed tool_display must be preserved
        assert 'important answer' in result
