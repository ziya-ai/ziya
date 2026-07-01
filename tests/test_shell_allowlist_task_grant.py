"""Slice B — task-scope grant honored at the base command allowlist.

The per-task ``shell_commands`` grant was originally consulted only inside
``ShellWriteChecker`` (the write-policy layer), which runs AFTER
``ShellServer.is_command_allowed`` (the base allowlist). So a command granted
to a task but absent from the base allowlist — e.g. ``dd`` / ``finger`` — was
denied at the allowlist with ``🚫 BLOCKED: 'dd' is not allowed`` and never
reached the layer that would honor the grant.

The fix makes ``is_command_allowed`` consult the active task scope at its
per-segment "no pattern matched" denial point, guarded so a grant can NEVER
unlock an ``always_blocked`` command (sudo/vi/etc.). The handler sets the task
scope BEFORE the allowlist check (not just around the write check) so the grant
is visible here.

Security-critical invariants pinned below:
  - a grant for a non-ceiling command absent from the base allowlist
    (``dd``/``finger``) is honored;
  - a grant for an ``always_blocked`` command (``sudo``) is REFUSED — the hard
    ceiling wins over any task grant;
  - with no active grant, those commands stay blocked (no silent widening).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def shell_server():
    """A ShellServer with default (floor) configuration, YOLO disabled."""
    old_yolo = os.environ.pop('YOLO_MODE', None)
    try:
        from app.mcp_servers.shell_server import ShellServer
        server = ShellServer()
        yield server
    finally:
        if old_yolo is not None:
            os.environ['YOLO_MODE'] = old_yolo


def _set_scope(server, commands):
    server.write_checker.set_task_scope({"shell_commands": commands})


def _clear_scope(server):
    server.write_checker.clear_task_scope()


class TestAllowlistHonorsTaskGrant:
    """``is_command_allowed`` consults the task-scope grant."""

    def test_dd_blocked_without_grant(self, shell_server):
        # Precondition: dd is not in the base allowlist, so it is denied
        # when no task grant is active.
        assert "dd" not in shell_server.allowed_commands
        allowed, reason = shell_server.is_command_allowed("dd if=/dev/zero of=/tmp/x")
        assert allowed is False
        assert "dd" in reason.lower() or "blocked" in reason.lower()

    def test_dd_allowed_with_grant(self, shell_server):
        _set_scope(shell_server, ["dd"])
        try:
            allowed, reason = shell_server.is_command_allowed("dd if=/dev/zero of=/tmp/x")
            assert allowed is True, f"task grant should unlock dd, got: {reason}"
        finally:
            _clear_scope(shell_server)

    def test_finger_allowed_with_grant(self, shell_server):
        _set_scope(shell_server, ["finger"])
        try:
            allowed, reason = shell_server.is_command_allowed("finger")
            assert allowed is True, f"task grant should unlock finger, got: {reason}"
        finally:
            _clear_scope(shell_server)

    def test_grant_clears_after_clear_task_scope(self, shell_server):
        _set_scope(shell_server, ["dd"])
        assert shell_server.is_command_allowed("dd if=/dev/zero of=/tmp/x")[0] is True
        _clear_scope(shell_server)
        assert shell_server.is_command_allowed("dd if=/dev/zero of=/tmp/x")[0] is False

    def test_grant_only_unlocks_the_granted_command(self, shell_server):
        # A grant for dd must not incidentally unlock finger.
        _set_scope(shell_server, ["dd"])
        try:
            assert shell_server.is_command_allowed("dd if=/dev/zero of=/tmp/x")[0] is True
            assert shell_server.is_command_allowed("finger")[0] is False
        finally:
            _clear_scope(shell_server)


class TestAlwaysBlockedCeilingPreserved:
    """A task grant can NEVER unlock an ``always_blocked`` command."""

    def test_grant_does_not_unlock_sudo(self, shell_server):
        # Security-critical: even with an explicit grant, sudo must stay blocked
        # because it is in the always_blocked hard-ceiling set.
        assert "sudo" in shell_server.wp_manager.policy.get("always_blocked", [])
        _set_scope(shell_server, ["sudo"])
        try:
            allowed, reason = shell_server.is_command_allowed("sudo whoami")
            assert allowed is False, "sudo must remain hard-blocked even with a task grant"
        finally:
            _clear_scope(shell_server)

    def test_grant_does_not_unlock_always_blocked_by_basename(self, shell_server):
        # The guard checks both the first token and its basename, so an
        # always_blocked command referenced by an absolute path is still refused.
        blocked = shell_server.wp_manager.policy.get("always_blocked", [])
        # vi is a representative always_blocked editor; skip if the policy
        # in this environment doesn't carry it.
        if "vi" not in blocked:
            pytest.skip("'vi' not in always_blocked in this environment")
        _set_scope(shell_server, ["vi"])
        try:
            allowed, _ = shell_server.is_command_allowed("/usr/bin/vi /etc/hosts")
            assert allowed is False, "always_blocked basename must win over a grant"
        finally:
            _clear_scope(shell_server)
