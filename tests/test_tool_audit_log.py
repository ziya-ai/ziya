"""
Tests for MCP tool execution audit logging.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestToolAuditLog:
    """Tests for app.utils.tool_audit_log."""

    @pytest.fixture(autouse=True)
    def reset_module_state(self):
        """Reset module-level state between tests."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = None
        mod._DISABLED = False
        yield
        mod._LOG_DIR = None
        mod._DISABLED = False

    def test_log_creates_jsonl_file(self, tmp_path):
        """A single log_tool_execution call should create a .jsonl file."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(
            tool_name="run_shell_command",
            args={"command": "ls -la"},
            result_status="ok",
            conversation_id="abc123def456",
            verified=True,
            duration_ms=42.5,
        )

        files = list(tmp_path.glob("tool_audit_*.jsonl"))
        assert len(files) == 1

        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["tool"] == "run_shell_command"
        assert entry["args"] == {"command": "ls -la"}
        assert entry["status"] == "ok"
        assert entry["conv"] == "abc123def456"
        assert entry["verified"] is True
        assert entry["ms"] == 42.5

    def test_log_truncates_long_args(self, tmp_path):
        """Argument values longer than 500 chars should be truncated."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        long_value = "x" * 1000
        mod.log_tool_execution(
            tool_name="file_read",
            args={"path": long_value},
        )

        files = list(tmp_path.glob("tool_audit_*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert len(entry["args"]["path"]) == 500

    def test_log_strips_internal_args(self, tmp_path):
        """Args prefixed with _ should not appear in the log."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(
            tool_name="run_shell_command",
            args={"command": "ls", "_workspace_path": "/home/user/project"},
        )

        files = list(tmp_path.glob("tool_audit_*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert "_workspace_path" not in entry["args"]
        assert "command" in entry["args"]

    def test_log_truncates_conversation_id(self, tmp_path):
        """Conversation IDs should be truncated to 12 chars."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(
            tool_name="test",
            args={},
            conversation_id="a-very-long-conversation-id-that-should-be-truncated",
        )

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert len(entry["conv"]) == 12

    def test_log_appends_multiple_entries(self, tmp_path):
        """Multiple calls on the same day should append to the same file."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        for i in range(5):
            mod.log_tool_execution(tool_name=f"tool_{i}", args={})

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 5

    def test_log_disabled_writes_nothing(self, tmp_path):
        """When _DISABLED=True, no files should be created."""
        import app.utils.tool_audit_log as mod
        mod._DISABLED = True
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={"x": "y"})

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 0

    def test_log_never_raises(self, tmp_path):
        """log_tool_execution must never raise, even with bad inputs."""
        import app.utils.tool_audit_log as mod
        # Point to a non-writable path
        mod._LOG_DIR = Path("/nonexistent/path/that/doesnt/exist")

        # Should not raise
        mod.log_tool_execution(tool_name="test", args={"key": "value"})

    def test_log_handles_none_args(self, tmp_path):
        """Passing None as args should not crash."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args=None)

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        entry = json.loads(files[0].read_text().strip())
        assert entry["args"] == {}

    def test_log_records_error_message(self, tmp_path):
        """Error messages should be recorded and truncated."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(
            tool_name="run_shell_command",
            args={"command": "bad_cmd"},
            result_status="error",
            error_message="E" * 500,
        )

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert entry["status"] == "error"
        assert len(entry["error"]) == 200  # Truncated to 200

    def test_log_records_unverified(self, tmp_path):
        """verified=False should be recorded distinctly from None."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={}, verified=False)

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert entry["verified"] is False

    def test_log_entry_has_iso_timestamp(self, tmp_path):
        """Timestamp should be ISO-8601 format."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={})

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        # ISO-8601 timestamps contain 'T' and end with timezone info
        assert "T" in entry["ts"]
