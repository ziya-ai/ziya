"""
CLI-task escalation DELIVERY via the _task_scope envelope (ASR F-001).

A signed CLI-task approval *authorizes* the escalation, but it must still REACH
the shell subprocess. The env path (apply_task_permissions) is clamped back to
the floor by the F-004 signature gate (a CLI approval is a scope_approvals/
record, not a ZIYA_SCOPE_SIG env signature), so the authorized grant is routed
through the _task_scope contextvars/envelope instead — consulted additively by
the shell server AFTER the clamp, never subject to it.

Pins:
  • task_runner.allow_to_task_scope — the allow-block → grant projection.
  • ShellWriteChecker._task_scope_grants_command — literal ``git`` grants any
    git subcommand; a ``re:`` grant scopes to exactly one git op.
  • ShellWriteChecker._task_scope_grants_write — write_patterns globs match.
  • fileio._check_task_scope_write — same glob match for the file_write builtin.
"""

import pytest

from app.task_runner import allow_to_task_scope, ALWAYS_BLOCKED
from app.mcp_servers.write_policy import ShellWriteChecker
from app.config.write_policy import WritePolicyManager


# ── allow_to_task_scope projection ──────────────────────────────────────────

def test_commands_become_literal_grants():
    cmds, writable = allow_to_task_scope({"commands": ["git", "python", "gh"]})
    assert cmds == ["git", "python", "gh"]
    assert writable == []


def test_always_blocked_commands_filtered():
    cmds, _ = allow_to_task_scope({"commands": ["git", "sudo", "vim"]})
    assert "sudo" not in cmds and "vim" not in cmds
    assert "git" in cmds


def test_git_operations_become_anchored_regex_grants():
    cmds, _ = allow_to_task_scope({"git_operations": ["push", "commit"]})
    assert any(c.startswith("re:^git") and "push" in c for c in cmds)
    assert any(c.startswith("re:^git") and "commit" in c for c in cmds)


def test_write_patterns_become_glob_entries():
    _, writable = allow_to_task_scope(
        {"write_patterns": ["*.toml", "CHANGELOG.md"]})
    assert {"pattern": "*.toml"} in writable
    assert {"pattern": "CHANGELOG.md"} in writable


def test_empty_or_none_allow():
    assert allow_to_task_scope(None) == ([], [])
    assert allow_to_task_scope({}) == ([], [])
    assert allow_to_task_scope("nope") == ([], [])


# ── command grant matching ──────────────────────────────────────────────────

def _checker(scope):
    c = ShellWriteChecker(WritePolicyManager())
    c.set_task_scope(scope)
    return c


def test_literal_git_grants_all_subcommands():
    c = _checker({"shell_commands": ["git"]})
    for cmd in ("git push", "git commit -m x", "git add .", "git rm f"):
        assert c._task_scope_grants_command(cmd) is True


def test_regex_git_op_scopes_to_one_subcommand():
    cmds, _ = allow_to_task_scope({"git_operations": ["status"]})
    c = _checker({"shell_commands": cmds})
    assert c._task_scope_grants_command("git status") is True
    assert c._task_scope_grants_command("git push") is False


def test_command_grant_empty_when_no_scope():
    c = _checker({})
    assert c._task_scope_grants_command("git push") is False


# ── write glob matching (shell path) ────────────────────────────────────────

def test_write_glob_matches_extension(tmp_path):
    c = _checker({"writable": [{"pattern": "*.toml"}],
                  "project_root": str(tmp_path)})
    assert c._task_scope_grants_write(str(tmp_path / "pyproject.toml")) is True


def test_write_glob_matches_basename(tmp_path):
    c = _checker({"writable": [{"pattern": "CHANGELOG.md"}],
                  "project_root": str(tmp_path)})
    assert c._task_scope_grants_write(str(tmp_path / "CHANGELOG.md")) is True


def test_write_glob_non_match(tmp_path):
    c = _checker({"writable": [{"pattern": "*.toml"}],
                  "project_root": str(tmp_path)})
    assert c._task_scope_grants_write(str(tmp_path / "app" / "main.py")) is False


def test_write_literal_path_entry_still_works(tmp_path):
    # {path} and {pattern} entries coexist; literal path path unaffected.
    c = _checker({"writable": [{"path": "setup.py"}],
                  "project_root": str(tmp_path)})
    assert c._task_scope_grants_write(str(tmp_path / "setup.py")) is True


# ── write glob matching (file_write builtin path) ───────────────────────────

def test_fileio_check_task_scope_write_glob(tmp_path):
    from app.context import (set_task_writable_paths,
                             reset_task_writable_paths)
    from app.mcp.tools import fileio
    tok = set_task_writable_paths([{"pattern": "*.toml"}])
    try:
        assert fileio._check_task_scope_write("pyproject.toml", str(tmp_path)) is True
        assert fileio._check_task_scope_write("app/main.py", str(tmp_path)) is False
    finally:
        reset_task_writable_paths(tok)
