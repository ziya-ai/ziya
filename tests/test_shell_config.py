"""
Tests for app.config.shell_config — default shell command allowlist.

Verifies that the canonical DEFAULT_SHELL_CONFIG contains expected
commands and that the merge logic from plugin providers works.
"""

import json
import pytest

from app.config.shell_config import DEFAULT_SHELL_CONFIG, get_default_shell_config
from app.mcp_servers.shell_server import ShellServer


class TestDefaultAllowedCommands:
    """Ensure baseline commands are always present in the allowlist."""

    EXPECTED_COMMANDS = [
        # Core file inspection
        "ls", "cat", "grep", "find", "head", "tail", "wc",
        # Text processing
        "sed", "awk", "sort", "uniq", "cut", "tr",
        # System info
        "uname", "hostname", "whoami", "id",
        # Network diagnostics
        "curl", "ping", "dig",
        # Process execution control
        "timeout",
        # Compressed file viewing
        "zcat", "zgrep",
    ]

    @pytest.mark.parametrize("cmd", EXPECTED_COMMANDS)
    def test_command_in_default_allowlist(self, cmd):
        assert cmd in DEFAULT_SHELL_CONFIG["allowedCommands"], (
            f"'{cmd}' should be in the default allowed commands"
        )

    def test_timeout_in_merged_config(self):
        """timeout should survive the plugin merge path."""
        merged = get_default_shell_config()
        assert "timeout" in merged["allowedCommands"]


class TestDefaultGitOperations:
    """Verify safe git operations are present."""

    EXPECTED_OPS = ["status", "log", "diff", "blame", "branch"]

    @pytest.mark.parametrize("op", EXPECTED_OPS)
    def test_git_op_in_default_config(self, op):
        assert op in DEFAULT_SHELL_CONFIG["safeGitOperations"]


class TestMergedConfig:
    """get_default_shell_config() returns a merged copy, not the original."""

    def test_returns_copy(self):
        config = get_default_shell_config()
        config["allowedCommands"].append("BOGUS_TEST_CMD")
        # Original must not be mutated
        assert "BOGUS_TEST_CMD" not in DEFAULT_SHELL_CONFIG["allowedCommands"]


class TestShellServerTimeout:
    """Verify the shell server handles the timeout parameter correctly."""

    @pytest.fixture
    def server(self):
        return ShellServer()

    def test_default_timeout_value(self, server):
        """Default timeout should come from COMMAND_TIMEOUT env (30s)."""
        assert server.command_timeout == 30

    def test_max_timeout_value(self, server):
        """Max timeout ceiling should default to 300s."""
        assert server.max_timeout == 300

    def test_schema_default_matches_server_default(self, server):
        """The tool schema's default value must match the runtime default."""
        # Simulate tools/list to get the schema
        import asyncio
        response = asyncio.get_event_loop().run_until_complete(
            server.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
            })
        )
        tools = response["result"]["tools"]
        shell_tool = next(t for t in tools if t["name"] == "run_shell_command")
        schema_default = shell_tool["inputSchema"]["properties"]["timeout"]["default"]
        assert schema_default == server.command_timeout

    def test_schema_description_mentions_max(self, server):
        """The schema description should document the max timeout."""
        import asyncio
        response = asyncio.get_event_loop().run_until_complete(
            server.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
            })
        )
        tools = response["result"]["tools"]
        shell_tool = next(t for t in tools if t["name"] == "run_shell_command")
        desc = shell_tool["inputSchema"]["properties"]["timeout"]["description"]
        assert "max:" in desc.lower() or str(server.max_timeout) in desc

    @pytest.mark.asyncio
    async def test_timeout_clamped_to_max(self, server):
        """Timeout values above max should be clamped, not rejected."""
        response = await server.handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "run_shell_command",
                "arguments": {"command": "echo hello", "timeout": 9999}
            }
        })
        # Should succeed (not error) — timeout was clamped internally
        assert "error" not in response or response["error"]["code"] != -32602

    @pytest.mark.asyncio
    async def test_timeout_zero_becomes_one(self, server):
        """Timeout=0 should be clamped to 1, not disable timeouts."""
        response = await server.handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "run_shell_command",
                "arguments": {"command": "echo hello", "timeout": 0}
            }
        })
        # Should succeed without hanging forever
        assert "result" in response
