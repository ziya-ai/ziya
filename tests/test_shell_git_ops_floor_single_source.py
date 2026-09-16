"""The git-ops floor has one source: ``DEFAULT_SHELL_CONFIG["safeGitOperations"]``.

``ShellServer`` installs a pattern per operation named in ``SAFE_GIT_OPERATIONS``.
When that variable is absent -- a server spawned without the gated channel, or
any test that strips the escalation keys -- it fell back to an inline literal
that had drifted from the config: ``tag``, ``ls-tree``, ``rev-parse``,
``describe``, ``rev-list``, ``shortlog``, ``whatchanged``, ``reflog``,
``stash list`` and ``config --get`` were missing.  ``scope_canonical`` derives
the *floor* from the config, so the two halves of the same gate disagreed
about what the floor was: a server reached through the canonical channel
admitted ``git rev-parse HEAD``; one reached without it refused it.

The fallback now reads the config the server already loads
(``self._effective_config``).  Anchored on the installed pattern keys, not on
the literal's spelling.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.config.scope_canonical import ESCALATION_ENV_KEYS
from app.config.shell_config import DEFAULT_SHELL_CONFIG


def _server_without_git_ops_env():
    from app.mcp_servers.shell_server import ShellServer

    env = {k: v for k, v in os.environ.items() if k not in ESCALATION_ENV_KEYS}
    env.pop("SAFE_GIT_OPERATIONS", None)
    env.pop("GIT_OPERATIONS_ENABLED", None)
    with patch.dict(os.environ, env, clear=True):
        return ShellServer()


def _key(op: str) -> str:
    return f"git_{op.replace(' ', '_').replace('-', '_')}"


@pytest.fixture(scope="module")
def server():
    return _server_without_git_ops_env()


def test_fallback_installs_every_configured_operation(server):
    expected = {_key(op) for op in DEFAULT_SHELL_CONFIG["safeGitOperations"]}
    installed = {k for k in server.safe_command_patterns if k.startswith("git_")}
    missing = expected - installed
    assert not missing, f"floor operations not installed without the env var: {sorted(missing)}"


def test_fallback_installs_nothing_beyond_the_config(server):
    expected = {_key(op) for op in DEFAULT_SHELL_CONFIG["safeGitOperations"]}
    installed = {k for k in server.safe_command_patterns if k.startswith("git_")}
    assert installed <= expected, sorted(installed - expected)


@pytest.mark.parametrize("cmd", [
    "git rev-parse HEAD",
    "git describe --tags",
    "git ls-tree HEAD",
    "git tag -l",
    "git stash list",
    "git config --get user.name",
])
def test_floor_operations_are_admitted_without_the_env_var(server, cmd):
    ok, reason = server.is_command_allowed(cmd)
    assert ok, reason


def test_env_var_still_narrows(monkeypatch):
    """Positive control for the other direction: an explicit value is honoured
    and can be narrower than the config."""
    from app.mcp_servers.shell_server import ShellServer

    env = {k: v for k, v in os.environ.items() if k not in ESCALATION_ENV_KEYS}
    env["SAFE_GIT_OPERATIONS"] = "status,log"
    with patch.dict(os.environ, env, clear=True):
        srv = ShellServer()
    installed = {k for k in srv.safe_command_patterns if k.startswith("git_")}
    assert installed == {"git_status", "git_log"}


def test_config_and_pattern_table_agree(server):
    """Every configured operation has a pattern to install, so no config entry
    is silently inert (the ``cat-file``/``check-ignore`` failure mode)."""
    for op in DEFAULT_SHELL_CONFIG["safeGitOperations"]:
        assert op in server.git_patterns, f"{op!r} is configured but has no pattern"
