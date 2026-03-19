"""
Tests for MCP tool execution audit logging.

Validates compliance with Amazon Security Event Logging Standard §5.1.4
and ASR requirement: "Implement Tool and Gateway Observability".
"""

import getpass
import json
import os
import socket
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
        mod._HOSTNAME = None
        mod._USERNAME = None
        yield
        mod._LOG_DIR = None
        mod._DISABLED = False
        mod._HOSTNAME = None
        mod._USERNAME = None

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
        assert entry["eventName"] == "run_shell_command"
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

    # ==================================================================
    # SEL Standard §5.1.4 — required fields
    # ==================================================================

    def test_sel_eventTime_is_iso8601_utc(self, tmp_path):
        """SEL §5.1.4.1: Timestamp must be ISO-8601 with timezone."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={})

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        ts = entry["eventTime"]
        # ISO-8601 contains 'T' separator and '+' or 'Z' timezone
        assert "T" in ts, f"Timestamp missing 'T': {ts}"
        assert "+" in ts or "Z" in ts, f"Timestamp missing timezone: {ts}"

    def test_sel_eventName_present(self, tmp_path):
        """SEL §5.1.4.2: eventName must identify the action requested."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="run_shell_command", args={"command": "ls"})

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert "eventName" in entry
        assert entry["eventName"] == "run_shell_command"

    def test_sel_userIdentity_present(self, tmp_path):
        """SEL §5.1.4.2: userIdentity must identify the caller."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={})

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert "userIdentity" in entry
        assert entry["userIdentity"] == getpass.getuser()

    def test_sel_principalType_present(self, tmp_path):
        """SEL §5.1.4.2: principalType must classify the identity."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={})

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert "principalType" in entry
        assert entry["principalType"] == "LocalUser"

    def test_sel_sourceHostname_present(self, tmp_path):
        """SEL §5.1.4.2: Source hostname must be recorded."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(tool_name="test", args={})

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert "sourceHostname" in entry
        assert entry["sourceHostname"] == socket.gethostname()

    def test_sel_all_mandatory_fields_present(self, tmp_path):
        """SEL: All five mandatory fields must be present in every entry."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        mod.log_tool_execution(
            tool_name="run_shell_command",
            args={"command": "whoami"},
            result_status="ok",
            conversation_id="conv-12345678",
            verified=True,
            duration_ms=55.0,
        )

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())

        # SEL mandatory fields
        assert "eventTime" in entry
        assert "eventName" in entry
        assert "userIdentity" in entry
        assert "principalType" in entry
        assert "sourceHostname" in entry

        # Operational fields
        assert entry["status"] == "ok"
        assert entry["conv"] == "conv-1234567"  # Truncated to 12
        assert entry["verified"] is True
        assert entry["ms"] == 55.0

    def test_log_format_is_json_one_per_line(self, tmp_path):
        """SEL §5.1.4.4: Log must be structured, machine-readable (JSONL)."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        for i in range(3):
            mod.log_tool_execution(tool_name=f"tool_{i}", args={"i": i})

        content = list(tmp_path.glob("*.jsonl"))[0].read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 3

        # Each line must be valid JSON
        for line in lines:
            entry = json.loads(line)  # Raises if not valid JSON
            assert isinstance(entry, dict)

    def test_log_is_append_only(self, tmp_path):
        """SEL §5.2.1.2: Log must be append-only."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        # Write first entry
        mod.log_tool_execution(tool_name="first", args={})
        files = list(tmp_path.glob("*.jsonl"))
        first_content = files[0].read_text()

        # Write second entry
        mod.log_tool_execution(tool_name="second", args={})
        second_content = files[0].read_text()

        # Second content must START with first content (append-only)
        assert second_content.startswith(first_content)
        assert second_content != first_content  # Something was added

    def test_no_credentials_in_log(self, tmp_path):
        """SEL §5.3.5: Auth credentials and encryption keys must not be logged."""
        import app.utils.tool_audit_log as mod
        mod._LOG_DIR = tmp_path

        # Simulate a tool call with sensitive args
        mod.log_tool_execution(
            tool_name="some_tool",
            args={
                "command": "echo hello",
                "_workspace_path": "/secret/path",
                "_session_key": "abcdef123456",
            },
        )

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        # Internal args (prefixed with _) must be stripped
        assert "_workspace_path" not in entry["args"]
        assert "_session_key" not in entry["args"]
        assert "command" in entry["args"]
