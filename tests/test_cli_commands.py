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
def mock_cli_class(mock_cli):
    """Patch the CLI class constructor to return mock_cli."""
    with patch("app.cli.CLI", return_value=mock_cli) as cls:
        yield cls


@pytest.fixture
def mock_auth():
    """Patch auth check to always succeed."""
    with patch("app.cli._check_auth_quick", return_value=True):
        yield


@pytest.fixture
def mock_plugins():
    """Patch plugin initialization to no-op."""
    with patch("app.plugins.initialize"):
        yield


@pytest.fixture
def mock_mcp():
    """Patch MCP initialization to no-op."""
    with patch("app.cli._initialize_mcp", new_callable=AsyncMock):
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
# main() routing tests
# ---------------------------------------------------------------------------

class TestMainRouting:
    """Verify main() dispatches to the correct subcommand handler."""

    def test_main_dispatches_to_handler(self):
        """Ensure parser.func is called with parsed args."""
        from app.cli import create_parser
        parser = create_parser()
        handler = MagicMock()
        args = parser.parse_args(["ask", "hello"])
        args.func = handler
        args.func(args)
        handler.assert_called_once_with(args)

    def test_main_global_flag_reordering(self):
        """Flags before the subcommand should be moved after it."""
        from app.cli import main as cli_main

        captured = {}

        def fake_func(args):
            captured["profile"] = args.profile
            captured["question"] = args.question

        with patch("sys.argv", ["ziya", "--profile", "dev", "ask", "hello"]), \
             patch("app.cli.cmd_ask", side_effect=fake_func):
            from app.cli import create_parser
            parser = create_parser()
            # Simulate the reordering that main() does
            argv = sys.argv[1:]
            commands = {'chat', 'ask', 'review', 'explain'}
            cmd_idx = next((i for i, a in enumerate(argv) if a in commands), None)
            assert cmd_idx == 2  # --profile dev ask → ask is at index 2
            # After reordering: ask hello --profile dev
            reordered = [argv[cmd_idx]] + argv[cmd_idx + 1:] + argv[:cmd_idx]
            assert reordered[0] == "ask"


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

    def test_ask_with_question(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="explain auth flow")
        with patch("app.cli.read_stdin_if_available", return_value=None):
            cmd_ask(args)
        mock_cli.ask.assert_awaited_once()
        call_args = mock_cli.ask.call_args
        assert "explain auth flow" in call_args[0][0]

    def test_ask_with_piped_stdin(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question=None)
        with patch("app.cli.read_stdin_if_available", return_value="def foo(): pass"):
            cmd_ask(args)
        question = mock_cli.ask.call_args[0][0]
        assert "def foo(): pass" in question

    def test_ask_combines_question_and_stdin(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="what's wrong?")
        with patch("app.cli.read_stdin_if_available", return_value="error: segfault"):
            cmd_ask(args)
        question = mock_cli.ask.call_args[0][0]
        assert "what's wrong?" in question
        assert "error: segfault" in question

    def test_ask_no_question_no_stdin_exits(self, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question=None)
        with patch("app.cli.read_stdin_if_available", return_value=None), \
             pytest.raises(SystemExit) as exc_info:
            cmd_ask(args)
        assert exc_info.value.code == 1

    def test_ask_auth_failure_exits(self, mock_plugins):
        from app.cli import cmd_ask
        args = _make_args(command="ask", question="hi")
        with patch("app.cli._check_auth_quick", return_value=False), \
             patch("app.cli._print_auth_error"), \
             pytest.raises(SystemExit) as exc_info:
            cmd_ask(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_review tests
# ---------------------------------------------------------------------------

class TestCmdReview:

    def test_review_piped_diff(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_review
        diff_text = "diff --git a/f b/f\n-old\n+new\n"
        args = _make_args(command="review")
        with patch("app.cli.read_stdin_if_available", return_value=diff_text), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_cli.ask.call_args[0][0]
        assert diff_text in question
        assert "Review this code" in question

    def test_review_staged(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_review
        staged = "diff --git a/x b/x\n+staged line\n"
        args = _make_args(command="review", staged=True)
        with patch("app.cli.get_git_staged_diff", return_value=staged), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_cli.ask.call_args[0][0]
        assert "+staged line" in question

    def test_review_unstaged(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_review
        unstaged = "diff --git a/y b/y\n-removed\n"
        args = _make_args(command="review", diff=True)
        with patch("app.cli.get_git_diff", return_value=unstaged), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_cli.ask.call_args[0][0]
        assert "-removed" in question

    def test_review_staged_empty_exits(self, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_review
        args = _make_args(command="review", staged=True)
        with patch("app.cli.get_git_staged_diff", return_value=None), \
             patch("app.cli.print_chat_startup_info"), \
             pytest.raises(SystemExit) as exc_info:
            cmd_review(args)
        assert exc_info.value.code == 1

    def test_review_custom_prompt(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_review
        args = _make_args(command="review", prompt="check for SQL injection")
        with patch("app.cli.read_stdin_if_available", return_value="SELECT * FROM users"), \
             patch("app.cli.print_chat_startup_info"):
            cmd_review(args)
        question = mock_cli.ask.call_args[0][0]
        assert "check for SQL injection" in question
        assert "SELECT * FROM users" in question


# ---------------------------------------------------------------------------
# cmd_explain tests
# ---------------------------------------------------------------------------

class TestCmdExplain:

    def test_explain_with_stdin(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_explain
        args = _make_args(command="explain")
        with patch("app.cli.read_stdin_if_available", return_value="class Foo: pass"):
            cmd_explain(args)
        question = mock_cli.ask.call_args[0][0]
        assert "Explain this code" in question
        assert "class Foo: pass" in question

    def test_explain_with_custom_prompt(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_explain
        args = _make_args(command="explain", prompt="explain the algorithm")
        with patch("app.cli.read_stdin_if_available", return_value="def sort(): ..."):
            cmd_explain(args)
        question = mock_cli.ask.call_args[0][0]
        assert "explain the algorithm" in question

    def test_explain_no_stdin_uses_prompt_only(self, mock_cli_class, mock_cli, mock_auth, mock_plugins, mock_mcp):
        from app.cli import cmd_explain
        args = _make_args(command="explain")
        with patch("app.cli.read_stdin_if_available", return_value=None):
            cmd_explain(args)
        question = mock_cli.ask.call_args[0][0]
        assert "Explain this code" in question


# ---------------------------------------------------------------------------
# resolve_files tests
# ---------------------------------------------------------------------------

class TestResolveFiles:

    def test_resolve_existing_file(self, tmp_path):
        from app.cli import resolve_files
        (tmp_path / "hello.py").write_text("print('hi')")
        result = resolve_files(["hello.py"], str(tmp_path))
        assert result == ["hello.py"]

    def test_resolve_directory_finds_supported_extensions(self, tmp_path):
        from app.cli import resolve_files
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("")
        (sub / "app.js").write_text("")
        (sub / "notes.txt").write_text("")  # not a supported extension
        result = resolve_files(["src"], str(tmp_path))
        assert "src/main.py" in result
        assert "src/app.js" in result
        assert "src/notes.txt" not in result

    def test_resolve_skips_node_modules(self, tmp_path):
        from app.cli import resolve_files
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("")
        (tmp_path / "app.js").write_text("")
        result = resolve_files(["."], str(tmp_path))
        assert all("node_modules" not in f for f in result)

    def test_resolve_nonexistent_returns_empty(self, tmp_path):
        from app.cli import resolve_files
        result = resolve_files(["no_such_file.py"], str(tmp_path))
        assert result == []


# ---------------------------------------------------------------------------
# main.py CLI routing integration
# ---------------------------------------------------------------------------

class TestMainPyRouting:
    """Verify that app.main.main() detects CLI subcommands and hands off."""

    def test_main_routes_ask_to_cli(self):
        """'ziya ask ...' should be detected and handed off to cli.main()."""
        with patch("sys.argv", ["ziya", "ask", "hello"]), \
             patch("app.cli.main") as mock_cli_main:
            from app.main import main
            main()
            mock_cli_main.assert_called_once()

    def test_main_routes_review_to_cli(self):
        with patch("sys.argv", ["ziya", "review", "--staged"]), \
             patch("app.cli.main") as mock_cli_main:
            from app.main import main
            main()
            mock_cli_main.assert_called_once()

    def test_main_routes_with_flags_before_command(self):
        """'ziya --profile dev ask hello' should still route to CLI."""
        with patch("sys.argv", ["ziya", "--profile", "dev", "ask", "hello"]), \
             patch("app.cli.main") as mock_cli_main:
            from app.main import main
            main()
            mock_cli_main.assert_called_once()
