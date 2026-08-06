"""
Centralized task-scope envelope construction (ASR F-001).

build_task_scope_envelope() reads the active task contextvars and returns the
shell _task_scope grant envelope (or None). Centralizing it in the manager makes
escalation delivery path-independent — every call_tool caller for the shell
server inherits the grant without each dispatch path building it. These pin the
helper's contract directly (call_tool itself is integration-tested live).
"""

import pytest

from app.mcp.manager import build_task_scope_envelope
from app.context import (
    set_task_shell_commands, reset_task_shell_commands,
    set_task_writable_paths, reset_task_writable_paths,
)


def test_none_when_no_grant_active():
    assert build_task_scope_envelope() is None


def test_builds_shell_command_grant():
    tok = set_task_shell_commands(["gh", "git"])
    try:
        env = build_task_scope_envelope()
    finally:
        reset_task_shell_commands(tok)
    assert env is not None
    assert env["shell_commands"] == ["gh", "git"]
    assert env["writable"] == [] and env["readable"] == []
    assert "project_root" in env


def test_builds_writable_grant():
    tok = set_task_writable_paths([{"pattern": "*.toml"}])
    try:
        env = build_task_scope_envelope()
    finally:
        reset_task_writable_paths(tok)
    assert env is not None
    assert {"pattern": "*.toml"} in env["writable"]


def test_combined_grants():
    t1 = set_task_shell_commands(["gh"])
    t2 = set_task_writable_paths([{"pattern": "*.cfg"}])
    try:
        env = build_task_scope_envelope()
    finally:
        reset_task_shell_commands(t1)
        reset_task_writable_paths(t2)
    assert env["shell_commands"] == ["gh"]
    assert {"pattern": "*.cfg"} in env["writable"]


def test_envelope_shape_matches_consumer_keys():
    """The envelope must carry exactly the keys the shell server's
    consumers read: writable, readable, shell_commands, project_root,
    shell_timeout_secs.

    Exact equality rather than a subset check is deliberate — a key
    nothing consumes is dead weight crossing JSON-RPC on every call, and
    a key a consumer expects but the builder omits is a silently dropped
    grant.  Both directions matter, so this test has to be updated
    whenever the envelope legitimately grows.
    """
    tok = set_task_shell_commands(["gh"])
    try:
        env = build_task_scope_envelope()
    finally:
        reset_task_shell_commands(tok)
    assert set(env.keys()) == {
        "writable", "readable", "shell_commands", "project_root",
        # Added with the per-task shell timeout grant; read by
        # ShellServer._timeout_bounds to raise the ceiling AND the
        # default for a single call.
        "shell_timeout_secs",
    }


def test_timeout_key_present_even_without_a_timeout_grant():
    """The key is always present (None when ungranted) rather than
    conditionally added, so the consumer's ``.get`` needs no special
    case for an older-shaped envelope."""
    tok = set_task_shell_commands(["gh"])
    try:
        env = build_task_scope_envelope()
    finally:
        reset_task_shell_commands(tok)
    assert env["shell_timeout_secs"] is None