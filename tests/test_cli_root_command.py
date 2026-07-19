"""
Tests for the CLI's /root (alias /cd) command handler — app.cli.CLI.cmd_root.

Covers:
  - show-current-root (no argument)
  - switching to a valid directory
  - rejecting a non-directory / already-at-root
  - clearing stale context files (paths were relative to the old root)
  - tearing down workspace-scoped MCP clients bound to the old root
  - command-spec/dispatch wiring (regression: an earlier revision
    registered the spec entry but never defined the handler, which
    would raise AttributeError on first use)
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cli import CLI, COMMAND_SPEC, CLI_DISPATCH


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Prevent tests from mutating the real cwd/env or touching a real MCP manager."""
    start_dir = tmp_path / "start"
    start_dir.mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(start_dir.resolve()))
    monkeypatch.delenv("ZIYA_EXPLICIT_ROOT", raising=False)
    monkeypatch.chdir(start_dir)
    return start_dir


@pytest.fixture
def cli():
    """A real CLI instance (constructor is side-effect-light — no network/model init)."""
    return CLI(files=[])


# ---------------------------------------------------------------------------
# Dispatch wiring
# ---------------------------------------------------------------------------

class TestCommandSpecWiring:
    """Regression: the spec entry must resolve to a real handler method."""

    def test_root_registered_in_command_spec(self):
        names = {e['name'] for e in COMMAND_SPEC}
        assert '/root' in names

    def test_cd_alias_registered(self):
        entry = next(e for e in COMMAND_SPEC if e['name'] == '/root')
        assert '/cd' in entry.get('aliases', [])

    def test_dispatch_map_points_at_real_handler(self):
        assert CLI_DISPATCH.get('/root') == 'cmd_root'
        assert CLI_DISPATCH.get('/cd') == 'cmd_root'

    def test_cmd_root_is_a_bound_method_on_cli(self, cli):
        """This is the exact regression that shipped once: the spec
        referenced cmd_root but the method didn't exist, so dispatch
        would raise AttributeError on first /root invocation."""
        handler = getattr(cli, CLI_DISPATCH['/root'], None)
        assert handler is not None
        assert callable(handler)


# ---------------------------------------------------------------------------
# Show current root
# ---------------------------------------------------------------------------

class TestShowRoot:

    def test_no_arg_prints_current_root(self, cli, _isolate_env, capsys):
        result = run(cli.cmd_root(""))
        assert result is True
        out = capsys.readouterr().out
        assert str(_isolate_env.resolve()) in out

    def test_whitespace_only_arg_treated_as_show(self, cli, _isolate_env, capsys):
        result = run(cli.cmd_root("   "))
        assert result is True
        out = capsys.readouterr().out
        assert str(_isolate_env.resolve()) in out
        # Must not have changed anything
        assert os.environ.get("ZIYA_EXPLICIT_ROOT") is None


# ---------------------------------------------------------------------------
# Changing root
# ---------------------------------------------------------------------------

class TestChangeRoot:

    def test_switches_to_valid_directory(self, cli, tmp_path, capsys):
        new_root = tmp_path / "new_project"
        new_root.mkdir()

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            result = run(cli.cmd_root(str(new_root)))

        assert result is True
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(new_root.resolve())
        assert os.environ["ZIYA_EXPLICIT_ROOT"] == "true"
        assert os.getcwd() == str(new_root.resolve())
        out = capsys.readouterr().out
        assert str(new_root.resolve()) in out

    def test_expands_user_and_relative_paths(self, cli, tmp_path, monkeypatch, capsys):
        new_root = tmp_path / "rel_project"
        new_root.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            result = run(cli.cmd_root("rel_project"))

        assert result is True
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(new_root.resolve())

    def test_rejects_nonexistent_path(self, cli, tmp_path, capsys):
        missing = tmp_path / "does_not_exist"
        before = os.environ["ZIYA_USER_CODEBASE_DIR"]

        result = run(cli.cmd_root(str(missing)))

        assert result is True  # command handled, just reported an error
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == before  # unchanged
        out = capsys.readouterr().out
        assert "Not a directory" in out

    def test_rejects_file_path_not_directory(self, cli, tmp_path):
        a_file = tmp_path / "not_a_dir.txt"
        a_file.write_text("hi")
        before = os.environ["ZIYA_USER_CODEBASE_DIR"]

        result = run(cli.cmd_root(str(a_file)))

        assert result is True
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == before

    def test_no_op_when_already_at_root(self, cli, _isolate_env, capsys):
        result = run(cli.cmd_root(str(_isolate_env)))
        assert result is True
        out = capsys.readouterr().out
        assert "Already at" in out
        # ZIYA_EXPLICIT_ROOT must not be set by a no-op
        assert os.environ.get("ZIYA_EXPLICIT_ROOT") is None


# ---------------------------------------------------------------------------
# Context-file invalidation
# ---------------------------------------------------------------------------

class TestContextFileClearing:

    def test_clears_stale_context_files_on_root_change(self, tmp_path, capsys):
        new_root = tmp_path / "new_project"
        new_root.mkdir()
        cli = CLI(files=["src/main.py", "README.md"])

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            run(cli.cmd_root(str(new_root)))

        assert cli.files == []
        out = capsys.readouterr().out
        assert "Clearing 2 context file" in out

    def test_does_not_touch_files_when_arg_is_empty(self, _isolate_env, capsys):
        cli = CLI(files=["src/main.py"])
        run(cli.cmd_root(""))
        assert cli.files == ["src/main.py"]

    def test_does_not_touch_files_on_rejected_path(self, tmp_path):
        cli = CLI(files=["src/main.py"])
        missing = tmp_path / "nope"
        run(cli.cmd_root(str(missing)))
        assert cli.files == ["src/main.py"]


# ---------------------------------------------------------------------------
# MCP workspace-scoped client teardown
# ---------------------------------------------------------------------------

class TestMcpWorkspaceTeardown:

    def test_disconnects_workspace_clients_bound_to_old_root(self, cli, _isolate_env, tmp_path):
        new_root = tmp_path / "new_project"
        new_root.mkdir()
        old_root = str(_isolate_env.resolve())

        stale_client = AsyncMock()
        other_client = AsyncMock()

        mock_manager = MagicMock()
        mock_manager.is_initialized = True
        mock_manager.workspace_scoped_clients = {
            "shell": {
                f"{old_root}::sess1": stale_client,
                "/some/other/root": other_client,
            }
        }
        mock_manager._workspace_instance_last_used = {"shell": {f"{old_root}::sess1": 123.0}}

        with patch("app.mcp.manager.get_mcp_manager", return_value=mock_manager):
            run(cli.cmd_root(str(new_root)))

        stale_client.disconnect.assert_awaited_once()
        other_client.disconnect.assert_not_called()
        assert f"{old_root}::sess1" not in mock_manager.workspace_scoped_clients["shell"]
        assert "/some/other/root" in mock_manager.workspace_scoped_clients["shell"]
        mock_manager.invalidate_tools_cache.assert_called_once()

    def test_survives_uninitialized_manager(self, cli, tmp_path):
        """get_mcp_manager() returning a not-yet-initialized manager must
        not raise — the root change should still succeed."""
        new_root = tmp_path / "new_project"
        new_root.mkdir()

        mock_manager = MagicMock()
        mock_manager.is_initialized = False

        with patch("app.mcp.manager.get_mcp_manager", return_value=mock_manager):
            result = run(cli.cmd_root(str(new_root)))

        assert result is True
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(new_root.resolve())

    def test_survives_manager_import_failure(self, cli, tmp_path, capsys):
        """If app.mcp.manager can't be imported/used for any reason, the
        root switch must still complete rather than crash the CLI."""
        new_root = tmp_path / "new_project"
        new_root.mkdir()

        with patch("app.mcp.manager.get_mcp_manager", side_effect=RuntimeError("boom")):
            result = run(cli.cmd_root(str(new_root)))

        assert result is True
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(new_root.resolve())
        out = capsys.readouterr().out
        assert "Root:" in out  # success line still printed


# ---------------------------------------------------------------------------
# /reset must undo a mid-session /root (/cd) change
# ---------------------------------------------------------------------------

class TestResetRestoresRoot:
    """Regression: a /root (/cd) change is session state and must not
    survive /reset — the root should return to how it stood at startup."""

    def test_reset_restores_startup_root(self, tmp_path, _isolate_env, capsys):
        start_root = str(_isolate_env.resolve())
        cli = CLI(files=[])
        new_root = tmp_path / "new_project"
        new_root.mkdir()

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            run(cli.cmd_root(str(new_root)))
            assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(new_root.resolve())
            capsys.readouterr()  # drain

            run(cli.cmd_reset(""))

        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == start_root
        assert os.getcwd() == start_root
        out = capsys.readouterr().out
        assert "Restored root" in out

    def test_reset_restores_explicit_root_flag(self, tmp_path, _isolate_env):
        """ZIYA_EXPLICIT_ROOT was unset at startup (see _isolate_env), so
        after reset it must be unset again — not left as 'true' from /root."""
        cli = CLI(files=[])
        new_root = tmp_path / "new_project"
        new_root.mkdir()

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            run(cli.cmd_root(str(new_root)))
            assert os.environ["ZIYA_EXPLICIT_ROOT"] == "true"
            run(cli.cmd_reset(""))

        assert os.environ.get("ZIYA_EXPLICIT_ROOT") is None

    def test_reset_without_root_change_leaves_root_alone(self, _isolate_env, capsys):
        """A plain /reset (no prior /cd) must not print a restore line or
        needlessly churn the root."""
        start_root = str(_isolate_env.resolve())
        cli = CLI(files=[])

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            run(cli.cmd_reset(""))

        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == start_root
        out = capsys.readouterr().out
        assert "Restored root" not in out

    def test_reset_tears_down_mcp_clients_bound_to_changed_root(self, tmp_path, _isolate_env):
        cli = CLI(files=[])
        new_root = tmp_path / "new_project"
        new_root.mkdir()
        changed = str(new_root.resolve())

        with patch("app.mcp.manager.get_mcp_manager", return_value=None):
            run(cli.cmd_root(str(new_root)))

        stale_client = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.is_initialized = True
        mock_manager.workspace_scoped_clients = {"shell": {f"{changed}::s": stale_client}}
        mock_manager._workspace_instance_last_used = {"shell": {f"{changed}::s": 1.0}}

        with patch("app.mcp.manager.get_mcp_manager", return_value=mock_manager):
            run(cli.cmd_reset(""))

        stale_client.disconnect.assert_awaited_once()
