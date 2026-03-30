"""
Tests for CLI command handlers: ask, review, explain, chat.

These test the argument parsing, stdin piping, and command routing
without hitting real models (all model calls are mocked).
"""

import argparse
import asyncio
import os
import sys
from io import StringIO
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**overrides):
    """Build a minimal argparse.Namespace that mirrors what create_parser produces."""
    defaults = dict(
        command=None,
        files=[],
        question=None,
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
        no_stream=True,       # default to non-streaming in tests
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
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Prevent tests from mutating the real environment."""
    monkeypatch.setenv("ZIYA_MODE", "chat")
    monkeypatch.setenv("ZIYA_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.setattr("os.getcwd", lambda: str(tmp_path))


@pytest.fixture
def mock_cli():
    """Return a mock CLI instance whose .ask() is an AsyncMock."""
    cli = MagicMock()
    cli.ask = AsyncMock(return_value="mocked response")
    cli.history = []
    cli.files = []
    return cli


@pytest.fixture
def mock_session(mock_cli):
    """Patch _create_cli_session to return mock_cli, bypassing init/auth/plugins."""
    with patch("app.cli._create_cli_session", return_value=mock_cli):
        yield mock_cli


@pytest.fixture
def mock_run():
    """Patch asyncio.run to capture but not actually execute the coroutine."""
    with patch("app.cli.asyncio") as mock_asyncio:
        # Make asyncio.run call the coroutine synchronously via a real loop
        def run_sync(coro):
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        mock_asyncio.run = run_sync
        yield mock_asyncio


@pytest.fixture
def mock_mcp_noop():
    """Patch _run_with_mcp to just await the inner coroutine directly."""
    async def passthrough(coro):
        return await coro
    with patch("app.cli._run_with_mcp", side_effect=passthrough):
        yield


# ---------------------------------------------------------------------------
# Argument parser tests
# ---------------------------------------------------------------------------

class TestCreateParser:
    """Tests for the argparse parser wiring."""

    def test_ask_subcommand_parsed(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["ask", "what is this?"])
        assert args.command == "ask"
        assert args.question == "what is this?"

    def test_ask_with_files(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["ask", "explain this", "foo.py", "bar.py"])
        assert args.question == "explain this"
        assert args.files == ["foo.py", "bar.py"]

    def test_review_subcommand_parsed(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["review"])
        assert args.command == "review"
        assert args.staged is False
        assert args.diff is False

    def test_review_staged_flag(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["review", "--staged"])
        assert args.staged is True

    def test_review_diff_flag(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["review", "--diff"])
        assert args.diff is True

    def test_review_custom_prompt(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["review", "--prompt", "check for SQL injection"])
        assert args.prompt == "check for SQL injection"

    def test_explain_subcommand_parsed(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["explain", "utils.py"])
        assert args.command == "explain"
        assert args.files == ["utils.py"]

    def test_chat_subcommand_parsed(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["chat"])
        assert args.command == "chat"

    def test_chat_with_files(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["chat", "src/", "lib/"])
        assert args.files == ["src/", "lib/"]

    def test_common_flags_on_subcommand(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["ask", "hi", "--model", "haiku", "--profile", "dev"])
        assert args.model == "haiku"
        assert args.profile == "dev"

    def test_no_command_gives_none(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args([])
        assert args.command is None


# ---------------------------------------------------------------------------
# stdin piping tests
# ---------------------------------------------------------------------------

class TestStdinPiping:

    def test_read_stdin_when_piped(self):
        """read_stdin_if_available() returns content when stdin is not a tty."""
        from app.cli import read_stdin_if_available
        fake_stdin = StringIO("hello from pipe")
        with patch("sys.stdin", fake_stdin), \
             patch.object(fake_stdin, "isatty", return_value=False):
            assert read_stdin_if_available() == "hello from pipe"

    def test_read_stdin_when_tty(self):
        """read_stdin_if_available() returns None for interactive terminals."""
        from app.cli import read_stdin_if_available
        fake_stdin = StringIO()
        with patch("sys.stdin", fake_stdin), \
             patch.object(fake_stdin, "isatty", return_value=True):
            assert read_stdin_if_available() is None


# ---------------------------------------------------------------------------
# git diff helpers
# ---------------------------------------------------------------------------

class TestGitHelpers:

    def test_get_git_staged_diff_success(self):
        from app.cli import get_git_staged_diff
        result = MagicMock(returncode=0, stdout="diff --git a/f b/f\n+hello\n")
        with patch("subprocess.run", return_value=result):
            assert get_git_staged_diff() == result.stdout

    def test_get_git_staged_diff_empty(self):
        from app.cli import get_git_staged_diff
        result = MagicMock(returncode=0, stdout="")
        with patch("subprocess.run", return_value=result):
            assert get_git_staged_diff() is None

    def test_get_git_diff_success(self):
        from app.cli import get_git_diff
        result = MagicMock(returncode=0, stdout="diff --git a/f b/f\n-old\n+new\n")
        with patch("subprocess.run", return_value=result):
            assert get_git_diff() == result.stdout

    def test_get_git_diff_subprocess_error(self):
        from app.cli import get_git_diff
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            assert get_git_diff() is None


# ---------------------------------------------------------------------------
# cmd_ask tests
# ---------------------------------------------------------------------------

class TestCmdAsk:

    def test_ask_with_question(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="explain auth flow")
        with patch("app.cli.read_stdin_if_available", return_value=None):
            cmd_ask(args)
        mock_session.ask.assert_awaited_once()
        call_args = mock_session.ask.call_args
        assert "explain auth flow" in call_args[0][0]

    def test_ask_with_piped_stdin(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question=None)
        with patch("app.cli.read_stdin_if_available", return_value="def foo(): pass"):
            cmd_ask(args)
        question = mock_session.ask.call_args[0][0]
        assert "def foo(): pass" in question

    def test_ask_combines_question_and_stdin(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="what's wrong?")
        with patch("app.cli.read_stdin_if_available", return_value="error: segfault"):
            cmd_ask(args)
        question = mock_session.ask.call_args[0][0]
        assert "what's wrong?" in question
        assert "error: segfault" in question

    def test_ask_no_question_no_stdin_exits(self, mock_session):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question=None)
        with patch("app.cli.read_stdin_if_available", return_value=None), \
             pytest.raises(SystemExit) as exc_info:
            cmd_ask(args)
        assert exc_info.value.code == 1

    def test_ask_auth_failure_exits(self):
        """When _init_and_authenticate fails (SystemExit), cmd_ask propagates it."""
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="hi")
        with patch("app.cli._create_cli_session", side_effect=SystemExit(1)), \
             pytest.raises(SystemExit) as exc_info:
            cmd_ask(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_review tests
# ---------------------------------------------------------------------------

class TestCmdReview:

    def test_review_piped_diff(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_review
        args = _make_args(command="review")
        with patch("app.cli.read_stdin_if_available", return_value="diff --git a/f b/f\n+new\n"), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_session.ask.call_args[0][0]
        assert "diff --git" in question

    def test_review_staged(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_review
        args = _make_args(command="review", staged=True)
        with patch("app.cli.get_git_staged_diff", return_value="staged diff"), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_session.ask.call_args[0][0]
        assert "staged diff" in question

    def test_review_unstaged(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_review
        args = _make_args(command="review", diff=True)
        with patch("app.cli.get_git_diff", return_value="unstaged diff"), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_session.ask.call_args[0][0]
        assert "unstaged diff" in question

    def test_review_custom_prompt(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_review
        args = _make_args(command="review", prompt="check for SQL injection")
        with patch("app.cli.read_stdin_if_available", return_value="some code"), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_session.ask.call_args[0][0]
        assert "SQL injection" in question


# ---------------------------------------------------------------------------
# cmd_explain tests
# ---------------------------------------------------------------------------

class TestCmdExplain:

    def test_explain_with_stdin(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_explain
        args = _make_args(command="explain", files=[])
        with patch("app.cli.read_stdin_if_available", return_value="def foo(): pass"):
            cmd_explain(args)
        question = mock_session.ask.call_args[0][0]
        assert "def foo(): pass" in question

    def test_explain_with_custom_prompt(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_explain
        args = _make_args(command="explain", prompt="explain the algorithm", files=[])
        with patch("app.cli.read_stdin_if_available", return_value="code here"):
            cmd_explain(args)
        question = mock_session.ask.call_args[0][0]
        assert "explain the algorithm" in question

    def test_explain_no_stdin_uses_prompt_only(self, mock_session, mock_run, mock_mcp_noop):
        from app.cli import cmd_explain
        args = _make_args(command="explain", prompt="what is this project?", files=[])
        with patch("app.cli.read_stdin_if_available", return_value=None):
            cmd_explain(args)
        question = mock_session.ask.call_args[0][0]
        assert "what is this project?" in question
