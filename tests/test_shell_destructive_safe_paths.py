"""
Tests for destructive commands on declared-safe write areas.

Verifies that rm, mv, cp, mkdir, etc. pass the command allowlist gate
and are correctly gated by the write policy checker on their target paths.

The two-gate design:
  Gate 1: Command allowlist (is_command_allowed) — is this binary permitted?
  Gate 2: Write policy (write_checker.check) — is the target path safe?

Destructive commands should pass gate 1 and be allowed/denied by gate 2
based on whether their targets fall under safe_write_paths or
allowed_write_patterns.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def shell_server():
    """Create a ShellServer with default configuration."""
    # Ensure we get a clean environment
    old_yolo = os.environ.pop('YOLO_MODE', None)
    try:
        from app.mcp_servers.shell_server import ShellServer
        server = ShellServer()
        yield server
    finally:
        if old_yolo is not None:
            os.environ['YOLO_MODE'] = old_yolo


# ── Gate 1: Destructive commands pass the command allowlist ─────────

class TestDestructiveCommandsPassAllowlist:
    """Destructive commands should be in the allowed_commands list so they
    can reach the write policy checker (gate 2)."""

    @pytest.mark.parametrize("cmd", [
        "rm", "rmdir", "mv", "cp", "mkdir", "chmod", "chown", "chgrp", "ln",
    ])
    def test_destructive_command_in_allowed_commands(self, shell_server, cmd):
        """Every destructive command should be in the allowed_commands list."""
        assert cmd in shell_server.allowed_commands, (
            f"'{cmd}' should be in allowed_commands so it can reach the write policy gate"
        )

    @pytest.mark.parametrize("cmd", [
        "rm /tmp/tempfile.txt",
        "mkdir /tmp/test-dir",
        "cp src/file.py /tmp/backup.py",
        "mv /tmp/old.txt /tmp/new.txt",
    ])
    def test_destructive_command_passes_allowlist(self, shell_server, cmd):
        """Destructive commands should pass the command-name allowlist check."""
        allowed, reason = shell_server.is_command_allowed(cmd)
        assert allowed, f"'{cmd}' should pass allowlist: {reason}"


# ── Gate 2: Write policy gates target paths ────────────────────────

class TestWritePolicyGatesDestructiveTargets:
    """Destructive commands should be blocked when targeting project files,
    but allowed when targeting safe write areas."""

    def test_rm_safe_path_allowed(self, shell_server):
        """rm targeting /tmp should pass both gates."""
        allowed, allow_reason = shell_server.is_command_allowed("rm /tmp/tempfile.txt")
        assert allowed, f"Allowlist should pass: {allow_reason}"

        write_ok, write_reason = shell_server.write_checker.check(
            "rm /tmp/tempfile.txt",
            shell_server._split_by_shell_operators,
        )
        assert write_ok, f"Write policy should allow /tmp: {write_reason}"

    def test_rm_ziya_path_allowed(self, shell_server):
        """rm targeting .ziya/ should pass both gates."""
        allowed, _ = shell_server.is_command_allowed("rm .ziya/tasks/old-plan/scratch.md")
        assert allowed

        write_ok, write_reason = shell_server.write_checker.check(
            "rm .ziya/tasks/old-plan/scratch.md",
            shell_server._split_by_shell_operators,
        )
        assert write_ok, f"Write policy should allow .ziya/: {write_reason}"

    def test_rm_project_file_blocked_by_write_policy(self, shell_server):
        """rm targeting a project source file should be blocked by write policy."""
        write_ok, write_reason = shell_server.write_checker.check(
            "rm src/main.py",
            shell_server._split_by_shell_operators,
        )
        assert not write_ok, "Write policy should block rm on project source files"
        assert "blocked" in write_reason.lower()

    def test_mkdir_safe_path_allowed(self, shell_server):
        """mkdir targeting .ziya/ should pass both gates."""
        allowed, _ = shell_server.is_command_allowed("mkdir .ziya/state")
        assert allowed

        write_ok, _ = shell_server.write_checker.check(
            "mkdir .ziya/state",
            shell_server._split_by_shell_operators,
        )
        assert write_ok

    def test_mkdir_project_dir_blocked(self, shell_server):
        """mkdir targeting a project directory should be blocked."""
        write_ok, _ = shell_server.write_checker.check(
            "mkdir src/new_module",
            shell_server._split_by_shell_operators,
        )
        assert not write_ok

    def test_cp_to_tmp_allowed(self, shell_server):
        """cp with destination in /tmp should pass."""
        write_ok, _ = shell_server.write_checker.check(
            "cp src/file.py /tmp/backup.py",
            shell_server._split_by_shell_operators,
        )
        assert write_ok

    def test_cp_to_project_blocked(self, shell_server):
        """cp with destination in project source should be blocked."""
        write_ok, _ = shell_server.write_checker.check(
            "cp /tmp/hack.py src/main.py",
            shell_server._split_by_shell_operators,
        )
        assert not write_ok

    def test_mv_within_tmp_allowed(self, shell_server):
        """mv within /tmp should pass."""
        write_ok, _ = shell_server.write_checker.check(
            "mv /tmp/old.txt /tmp/new.txt",
            shell_server._split_by_shell_operators,
        )
        assert write_ok

    def test_mv_to_project_blocked(self, shell_server):
        """mv into project source should be blocked."""
        write_ok, _ = shell_server.write_checker.check(
            "mv /tmp/file.py src/file.py",
            shell_server._split_by_shell_operators,
        )
        assert not write_ok

    def test_rm_with_flags_safe_path_allowed(self, shell_server):
        """rm -rf on a safe path should pass."""
        allowed, _ = shell_server.is_command_allowed("rm -rf /tmp/build-cache")
        assert allowed

        write_ok, _ = shell_server.write_checker.check(
            "rm -rf /tmp/build-cache",
            shell_server._split_by_shell_operators,
        )
        assert write_ok

    def test_rm_with_flags_project_blocked(self, shell_server):
        """rm -rf on project files should be blocked."""
        write_ok, _ = shell_server.write_checker.check(
            "rm -rf src/",
            shell_server._split_by_shell_operators,
        )
        assert not write_ok


# ── Always-blocked commands still blocked ──────────────────────────

class TestAlwaysBlockedStillBlocked:
    """Commands in always_blocked should NOT pass the allowlist,
    even though destructive commands now do."""

    @pytest.mark.parametrize("cmd", [
        "sudo rm /tmp/file.txt",
        "vim .ziya/notes.md",
        "nano /tmp/file.txt",
    ])
    def test_always_blocked_not_in_allowlist(self, shell_server, cmd):
        """Always-blocked commands should fail the allowlist check."""
        allowed, _ = shell_server.is_command_allowed(cmd)
        assert not allowed


# ── End-to-end via handle_request ──────────────────────────────────

class TestEndToEndViaHandleRequest:
    """Test the full request path through handle_request to verify
    that both gates are applied in sequence."""

    @pytest.mark.asyncio
    async def test_rm_tmp_succeeds_end_to_end(self, shell_server):
        """rm of a file in /tmp should succeed through the full request path."""
        import tempfile
        # Use /tmp directly (a declared safe_write_path) rather than pytest's
        # tmp_path which resolves to /private/var/folders/... on macOS.
        fd, target_path = tempfile.mkstemp(dir="/tmp", prefix="ziya_test_rm_")
        os.close(fd)
        with open(target_path, "w") as f:
            f.write("delete me")
        assert os.path.exists(target_path)

        try:
            response = await shell_server.handle_request({
                "method": "tools/call",
                "id": 1,
                "params": {
                    "name": "run_shell_command",
                    "arguments": {"command": f"rm {target_path}"},
                },
            })

            # Should succeed (no error key)
            assert "error" not in response, f"Expected success but got: {response.get('error', {}).get('message', '')}"
            assert "result" in response
            assert not os.path.exists(target_path), "File should have been removed"
        finally:
            # Cleanup in case test fails before rm executes
            if os.path.exists(target_path):
                os.unlink(target_path)

    @pytest.mark.asyncio
    async def test_rm_project_file_blocked_end_to_end(self, shell_server):
        """rm of a project source file should be blocked with WRITE BLOCKED message."""
        response = await shell_server.handle_request({
            "method": "tools/call",
            "id": 2,
            "params": {
                "name": "run_shell_command",
                "arguments": {"command": "rm src/important.py"},
            },
        })

        assert "error" in response
        msg = response["error"]["message"]
        assert "WRITE BLOCKED" in msg or "blocked" in msg.lower()

    @pytest.mark.asyncio
    async def test_mkdir_tmp_succeeds_end_to_end(self, shell_server):
        """mkdir inside /tmp should succeed through the full request path."""
        import tempfile
        target_dir = os.path.join("/tmp", f"ziya_test_mkdir_{os.getpid()}")
        # Ensure it doesn't exist
        if os.path.exists(target_dir):
            os.rmdir(target_dir)

        try:
            response = await shell_server.handle_request({
                "method": "tools/call",
                "id": 3,
                "params": {
                    "name": "run_shell_command",
                    "arguments": {"command": f"mkdir {target_dir}"},
                },
            })

            assert "error" not in response, f"Expected success but got: {response.get('error', {}).get('message', '')}"
            assert os.path.isdir(target_dir)
        finally:
            if os.path.exists(target_dir):
                os.rmdir(target_dir)
