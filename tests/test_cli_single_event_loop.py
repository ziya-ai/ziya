"""
Tests for single-event-loop MCP initialization in CLI commands.

Regression: cmd_ask, cmd_review, cmd_explain, and cmd_task previously called
asyncio.run(_initialize_mcp()) followed by asyncio.run(cli.ask(...)), creating
two separate event loops. The first loop established MCP connections, then was
destroyed. The second loop had no active MCP connections, so tools silently failed.

The fix introduces _run_with_mcp() which awaits both in a single event loop,
matching the pattern cmd_chat already used via _run_async_cli().
"""

import argparse
import asyncio
import os
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def _make_args(**overrides):
    """Minimal argparse.Namespace mirroring create_parser output."""
    defaults = dict(
        command=None,
        files=[],
        question="test question",
        staged=False,
        diff=False,
        prompt=None,
        resume=False,
        ephemeral=False,
        model=None,
        profile=None,
        region=None,
        endpoint=None,
        root=None,
        no_stream=True,
        debug=False,
        temperature=None,
        top_p=None,
        top_k=None,
        max_output_tokens=None,
        thinking_level=None,
        include=[],
        exclude=[],
        include_only=[],
        model_id=None,
        task_name=None,
        list_tasks=False,
        show=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZIYA_MODE", "chat")
    monkeypatch.setenv("ZIYA_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))


class TestRunWithMcp:
    """Verify _run_with_mcp runs init and the coroutine in one event loop."""

    @pytest.mark.asyncio
    async def test_run_with_mcp_calls_init_then_coro(self):
        """_run_with_mcp must await _initialize_mcp then the user coroutine."""
        from app.cli import _run_with_mcp

        call_order = []

        async def fake_init():
            call_order.append("init")

        async def fake_work():
            call_order.append("work")
            return "result"

        with patch("app.cli._initialize_mcp", side_effect=fake_init):
            result = await _run_with_mcp(fake_work())

        assert call_order == ["init", "work"]
        assert result == "result"

    @pytest.mark.asyncio
    async def test_run_with_mcp_propagates_return_value(self):
        from app.cli import _run_with_mcp

        async def coro():
            return 42

        with patch("app.cli._initialize_mcp", new_callable=AsyncMock):
            assert await _run_with_mcp(coro()) == 42

    @pytest.mark.asyncio
    async def test_run_with_mcp_same_event_loop(self):
        """Both _initialize_mcp and the coroutine must share the same loop."""
        from app.cli import _run_with_mcp

        loops_seen = []

        async def capture_loop_init():
            loops_seen.append(id(asyncio.get_running_loop()))

        async def capture_loop_work():
            loops_seen.append(id(asyncio.get_running_loop()))

        with patch("app.cli._initialize_mcp", side_effect=capture_loop_init):
            await _run_with_mcp(capture_loop_work())

        assert len(loops_seen) == 2
        assert loops_seen[0] == loops_seen[1], "MCP init and work coroutine ran on different event loops"


class TestSingleEventLoopInCommands:
    """
    Verify each CLI command uses a single asyncio.run() call.

    The key invariant: asyncio.run() must be called exactly ONCE per command,
    wrapping _run_with_mcp (or _run_async_cli for chat). If it's called twice,
    MCP connections from the first run are destroyed before the second run.
    """

    def _count_asyncio_runs(self, func, args):
        """Call a CLI command function and count how many times asyncio.run() is invoked."""
        run_count = 0
        original_run = asyncio.run

        def counting_run(coro, **kwargs):
            nonlocal run_count
            run_count += 1
            # Don't actually run — we just want to count
            # Close the coroutine to avoid RuntimeWarning
            coro.close()

        patches = [
            patch("app.cli.setup_env"),
            patch("app.cli._check_auth_quick", return_value=True),
            patch("app.cli.resolve_files", return_value=[]),
            patch("app.cli.read_stdin_if_available", return_value=None),
            patch("app.cli.CLI"),
            patch("asyncio.run", side_effect=counting_run),
        ]
        for p in patches:
            p.start()
        try:
            # Suppress plugins import since test env may not have them
            with patch("app.plugins.initialize"):
                func(args)
        finally:
            for p in patches:
                p.stop()

        return run_count

    def test_cmd_ask_single_run(self):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="hello")
        count = self._count_asyncio_runs(cmd_ask, args)
        assert count == 1, f"cmd_ask calls asyncio.run() {count} times (expected 1)"

    def test_cmd_review_single_run(self):
        from app.cli import cmd_review
        args = _make_args(command="review")
        with patch("app.cli.print_chat_startup_info"):
            count = self._count_asyncio_runs(cmd_review, args)
        assert count == 1, f"cmd_review calls asyncio.run() {count} times (expected 1)"

    def test_cmd_explain_single_run(self):
        from app.cli import cmd_explain
        args = _make_args(command="explain")
        count = self._count_asyncio_runs(cmd_explain, args)
        assert count == 1, f"cmd_explain calls asyncio.run() {count} times (expected 1)"

    def test_cmd_task_single_run(self):
        from app.cli import cmd_task
        args = _make_args(command="task", task_name="test_task")

        fake_tasks = {"test_task": {"prompt": "do stuff", "description": "test"}}
        with patch("app.task_runner.load_tasks", return_value=fake_tasks):
            count = self._count_asyncio_runs(cmd_task, args)
        assert count == 1, f"cmd_task calls asyncio.run() {count} times (expected 1)"
