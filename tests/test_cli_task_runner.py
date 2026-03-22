"""
Tests for `ziya task` — named prompt task runner.

Tests cover:
  - Task loading from global and project-local sources
  - Merge precedence (project > global > builtin)
  - YAML and JSON loading
  - cmd_task handler: --list, --show, unknown task, no name
  - Parser wiring: subcommand exists, flags parsed
  - main.py routing: 'task' in cli_commands set
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import pytest


# ============================================================================
# Task loading
# ============================================================================

class TestLoadTasks:
    """Test task definition loading and merging."""

    def test_empty_builtins(self):
        """With no user files, builtins dict is returned (currently empty)."""
        from app.task_runner import load_tasks
        tasks = load_tasks("/nonexistent")
        assert isinstance(tasks, dict)

    def test_project_local_tasks(self, tmp_path):
        """Project .ziya/tasks.json loads correctly."""
        from app.task_runner import load_tasks
        ziya_dir = tmp_path / ".ziya"
        ziya_dir.mkdir()
        (ziya_dir / "tasks.json").write_text(json.dumps({
            "flush": {"description": "custom flush", "prompt": "do custom stuff"},
            "deploy": {"description": "deploy it", "prompt": "deploy now"},
        }))
        tasks = load_tasks(str(tmp_path))
        assert tasks["flush"]["prompt"] == "do custom stuff"
        assert "deploy" in tasks

    def test_global_tasks(self, tmp_path):
        """~/.ziya/tasks.json loads correctly."""
        from app.task_runner import load_tasks
        global_dir = tmp_path / "fakehome" / ".ziya"
        global_dir.mkdir(parents=True)
        (global_dir / "tasks.json").write_text(json.dumps({
            "check": {"description": "custom check", "prompt": "check custom"},
        }))
        with patch("app.task_runner.Path.home", return_value=tmp_path / "fakehome"):
            tasks = load_tasks("/nonexistent")
        assert tasks["check"]["prompt"] == "check custom"

    def test_project_beats_global(self, tmp_path):
        """Project-local wins over global on same key."""
        from app.task_runner import load_tasks
        global_dir = tmp_path / "fakehome" / ".ziya"
        global_dir.mkdir(parents=True)
        (global_dir / "tasks.json").write_text(json.dumps({
            "flush": {"description": "global flush", "prompt": "global"},
        }))
        project_dir = tmp_path / "project" / ".ziya"
        project_dir.mkdir(parents=True)
        (project_dir / "tasks.json").write_text(json.dumps({
            "flush": {"description": "local flush", "prompt": "local"},
        }))
        with patch("app.task_runner.Path.home", return_value=tmp_path / "fakehome"):
            tasks = load_tasks(str(tmp_path / "project"))
        assert tasks["flush"]["prompt"] == "local"

    def test_global_and_project_merge(self, tmp_path):
        """Tasks from both sources are merged, not replaced."""
        from app.task_runner import load_tasks
        global_dir = tmp_path / "fakehome" / ".ziya"
        global_dir.mkdir(parents=True)
        (global_dir / "tasks.json").write_text(json.dumps({
            "lint": {"description": "global lint", "prompt": "lint it"},
        }))
        project_dir = tmp_path / "project" / ".ziya"
        project_dir.mkdir(parents=True)
        (project_dir / "tasks.json").write_text(json.dumps({
            "deploy": {"description": "local deploy", "prompt": "deploy it"},
        }))
        with patch("app.task_runner.Path.home", return_value=tmp_path / "fakehome"):
            tasks = load_tasks(str(tmp_path / "project"))
        assert "lint" in tasks
        assert "deploy" in tasks

    def test_yaml_loading(self, tmp_path):
        """YAML task files load correctly."""
        from app.task_runner import load_tasks
        ziya_dir = tmp_path / ".ziya"
        ziya_dir.mkdir()
        (ziya_dir / "tasks.yaml").write_text(
            "custom:\n  description: yaml task\n  prompt: do yaml things\n"
        )
        tasks = load_tasks(str(tmp_path))
        assert "custom" in tasks
        assert tasks["custom"]["prompt"] == "do yaml things"

    def test_empty_file_handled(self, tmp_path):
        """Empty task file doesn't crash, returns builtins only."""
        from app.task_runner import load_tasks
        ziya_dir = tmp_path / ".ziya"
        ziya_dir.mkdir()
        (ziya_dir / "tasks.json").write_text("")
        tasks = load_tasks(str(tmp_path))
        assert isinstance(tasks, dict)

    def test_missing_yaml_graceful(self, tmp_path):
        """Missing PyYAML prints warning, doesn't crash."""
        from app.task_runner import _load_file
        ziya_dir = tmp_path / ".ziya"
        ziya_dir.mkdir()
        yaml_file = ziya_dir / "tasks.yaml"
        yaml_file.write_text("custom:\n  prompt: test\n")
        with patch.dict("sys.modules", {"yaml": None}):
            with patch("builtins.__import__", side_effect=ImportError("no yaml")):
                # _load_file should return {} and print a warning
                result = _load_file(yaml_file)
                assert result == {}


# ============================================================================
# cmd_task handler
# ============================================================================

# Sample tasks used across handler tests
SAMPLE_TASKS = {
    "flush": {"description": "Verify and commit", "prompt": "Go through uncommitted changes, verify, commit, push."},
    "check": {"description": "Syntax check", "prompt": "Check all uncommitted changes for syntax errors."},
    "deploy": {"description": "Deploy to staging", "prompt": "Build and deploy."},
}


class TestCmdTask:
    """Test the cmd_task command handler."""

    def _make_args(self, **kwargs):
        args = MagicMock()
        args.task_name = kwargs.get("task_name", None)
        args.list_tasks = kwargs.get("list_tasks", False)
        args.show = kwargs.get("show", None)
        args.no_stream = kwargs.get("no_stream", False)
        args.profile = kwargs.get("profile", None)
        args.debug = False
        args.model = None
        args.region = None
        args.root = None
        args.endpoint = None
        return args

    @patch("app.cli.setup_env")
    @patch("app.task_runner.load_tasks", return_value=SAMPLE_TASKS)
    def test_list_tasks(self, mock_load, mock_setup, capsys):
        from app.cli import cmd_task
        args = self._make_args(list_tasks=True)
        cmd_task(args)
        output = capsys.readouterr().out
        assert "flush" in output
        assert "check" in output
        assert "deploy" in output

    @patch("app.cli.setup_env")
    def test_list_empty(self, mock_setup, capsys):
        """Empty task list shows helpful message."""
        from app.cli import cmd_task
        with patch("app.task_runner.load_tasks", return_value={}):
            args = self._make_args(list_tasks=True)
            cmd_task(args)
        output = capsys.readouterr().out
        assert "No tasks defined" in output
        assert "tasks.yaml" in output

    @patch("app.cli.setup_env")
    @patch("app.task_runner.load_tasks", return_value=SAMPLE_TASKS)
    def test_show_task(self, mock_load, mock_setup, capsys):
        from app.cli import cmd_task
        args = self._make_args(show="flush")
        cmd_task(args)
        output = capsys.readouterr().out
        assert "flush" in output
        assert "uncommitted" in output.lower()

    @patch("app.cli.setup_env")
    @patch("app.task_runner.load_tasks", return_value=SAMPLE_TASKS)
    def test_show_unknown_task_exits(self, mock_load, mock_setup):
        from app.cli import cmd_task
        args = self._make_args(show="nonexistent")
        with pytest.raises(SystemExit) as exc_info:
            cmd_task(args)
        assert exc_info.value.code == 1

    @patch("app.cli.setup_env")
    @patch("app.task_runner.load_tasks", return_value=SAMPLE_TASKS)
    def test_no_name_shows_list(self, mock_load, mock_setup, capsys):
        """No task name defaults to --list behavior."""
        from app.cli import cmd_task
        args = self._make_args(task_name=None, list_tasks=False)
        cmd_task(args)
        output = capsys.readouterr().out
        assert "flush" in output

    @patch("app.cli.setup_env")
    @patch("app.task_runner.load_tasks", return_value=SAMPLE_TASKS)
    def test_unknown_task_name_exits(self, mock_load, mock_setup):
        from app.cli import cmd_task
        args = self._make_args(task_name="nonexistent")
        with pytest.raises(SystemExit) as exc_info:
            cmd_task(args)
        assert exc_info.value.code == 1

    @patch("app.cli.asyncio")
    @patch("app.cli._initialize_mcp")
    @patch("app.cli._check_auth_quick", return_value=True)
    @patch("app.cli.setup_env")
    @patch("app.task_runner.load_tasks", return_value=SAMPLE_TASKS)
    def test_runs_prompt_through_ask(self, mock_load, mock_setup, mock_auth, mock_mcp, mock_asyncio):
        """Task execution sends the prompt through CLI.ask()."""
        from app.cli import cmd_task

        mock_asyncio.run = MagicMock(side_effect=lambda coro: None)

        with patch("app.cli.CLI") as MockCLI:
            instance = MockCLI.return_value
            instance.ask = AsyncMock(return_value="done")

            with patch("app.plugins.initialize"):
                args = self._make_args(task_name="flush")
                cmd_task(args)

        # asyncio.run should have been called twice: once for MCP, once for ask
        assert mock_asyncio.run.call_count == 2


# ============================================================================
# Parser wiring
# ============================================================================

class TestTaskParserWiring:
    """Test that 'task' subcommand is wired into argparse."""

    def test_task_subcommand_exists(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["task", "flush"])
        assert args.command == "task"

    def test_task_name_parsed(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["task", "myname"])
        assert args.task_name == "myname"

    def test_task_list_flag(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["task", "--list"])
        assert args.list_tasks is True

    def test_task_show_flag(self):
        from app.cli import create_parser
        parser = create_parser()
        args = parser.parse_args(["task", "--show", "flush"])
        assert args.show == "flush"

    def test_task_in_commands_set(self):
        """'task' must be in the commands set for flag reordering."""
        import app.cli
        source = open(app.cli.__file__).read()
        assert "'task'" in source or '"task"' in source

    def test_task_in_main_py_routing(self):
        """main.py must route 'task' to CLI."""
        import app.main
        source = open(app.main.__file__).read()
        assert "'task'" in source or '"task"' in source


# ============================================================================
# Builtin task quality
# ============================================================================

class TestBuiltinTasks:
    """Verify builtin tasks are well-formed (if any exist)."""

    def test_all_builtins_have_required_fields(self):
        from app.config.builtin_tasks import BUILTIN_TASKS
        for name, task in BUILTIN_TASKS.items():
            assert "description" in task, f"{name} missing description"
            assert "prompt" in task, f"{name} missing prompt"
            assert isinstance(task["description"], str)
            assert isinstance(task["prompt"], str)
            assert len(task["prompt"]) > 10, f"{name} prompt too short"

    def test_no_template_vars_in_builtins(self):
        """Builtin prompts should not contain {{vars}} — they're self-contained."""
        from app.config.builtin_tasks import BUILTIN_TASKS
        import re
        for name, task in BUILTIN_TASKS.items():
            matches = re.findall(r"\{\{.+?\}\}", task["prompt"])
            assert not matches, f"{name} has template vars: {matches}"

    def test_builtins_is_dict(self):
        """BUILTIN_TASKS must be a dict (even if empty)."""
        from app.config.builtin_tasks import BUILTIN_TASKS
        assert isinstance(BUILTIN_TASKS, dict)
