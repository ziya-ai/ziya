"""
Tests for app.mcp.signing — HMAC tool result signing and verification.

Covers:
  - sign → verify round-trip
  - Tampered content fails verification
  - Stale timestamp (>5 min) fails
  - Missing metadata fields fail
  - Tool name mismatch detection
  - strip_signature_metadata removes internal keys
  - Non-dict inputs handled gracefully
"""

import json
import time
from unittest.mock import patch

import pytest

from app.mcp.signing import (
    get_session_secret,
    sign_tool_result,
    verify_tool_result,
    strip_signature_metadata,
)


@pytest.fixture(autouse=True)
def fresh_secret():
    """Reset the session secret before each test so tests are isolated."""
    import app.mcp.signing as mod
    mod._session_secret = None
    yield


# ── Round-trip ─────────────────────────────────────────────────────

class TestRoundTrip:

    def test_sign_verify_succeeds(self):
        result = sign_tool_result(
            tool_name="mcp_run_shell_command",
            arguments={"command": "ls -la"},
            result="file1.py\nfile2.py",
            conversation_id="conv-123",
        )
        ok, err = verify_tool_result(result, "mcp_run_shell_command")
        assert ok, f"Verification should pass: {err}"
        assert err is None

    def test_sign_verify_dict_result(self):
        """Verify works when result is already a dict with 'content' key."""
        original_result = {
            "content": [{"type": "text", "text": "hello world"}]
        }
        signed = sign_tool_result(
            tool_name="mcp_fetch",
            arguments={"url": "https://example.com"},
            result=original_result,
            conversation_id="conv-456",
        )
        ok, err = verify_tool_result(signed, "mcp_fetch")
        assert ok, f"Verification should pass for dict result: {err}"

    def test_sign_preserves_content(self):
        signed = sign_tool_result(
            tool_name="test_tool",
            arguments={"key": "value"},
            result="output data",
        )
        assert "content" in signed
        assert signed["content"][0]["text"] == "output data"


# ── Tampered content ──────────────────────────────────────────────

class TestTampering:

    def test_modified_content_fails(self):
        signed = sign_tool_result(
            tool_name="mcp_run_shell_command",
            arguments={"command": "whoami"},
            result="safe_user",
        )
        # Tamper with the content
        signed["content"] = [{"type": "text", "text": "root"}]

        ok, err = verify_tool_result(signed, "mcp_run_shell_command")
        assert not ok
        assert "failed" in err.lower() or "tamper" in err.lower()

    def test_modified_signature_fails(self):
        signed = sign_tool_result(
            tool_name="test_tool",
            arguments={},
            result="data",
        )
        signed["_signature"] = "0" * 64  # Fake signature

        ok, err = verify_tool_result(signed)
        assert not ok

    def test_modified_arguments_fails(self):
        signed = sign_tool_result(
            tool_name="mcp_run_shell_command",
            arguments={"command": "ls"},
            result="safe output",
        )
        # Tamper with the stored arguments
        signed["_arguments"] = {"command": "rm -rf /"}

        ok, err = verify_tool_result(signed, "mcp_run_shell_command")
        assert not ok


# ── Stale timestamps ──────────────────────────────────────────────

class TestStaleness:

    def test_stale_timestamp_fails(self):
        signed = sign_tool_result(
            tool_name="test_tool",
            arguments={},
            result="data",
        )
        # Set timestamp to 10 minutes ago
        signed["_timestamp"] = time.time() - 600

        ok, err = verify_tool_result(signed)
        assert not ok
        assert "stale" in err.lower()

    def test_recent_timestamp_passes(self):
        signed = sign_tool_result(
            tool_name="test_tool",
            arguments={},
            result="data",
        )
        # Timestamp is set automatically to now, should be fine
        ok, err = verify_tool_result(signed)
        assert ok


# ── Missing metadata ──────────────────────────────────────────────

class TestMissingMetadata:

    def test_missing_signature_fails(self):
        ok, err = verify_tool_result({"content": [{"type": "text", "text": "data"}]})
        assert not ok
        assert "missing signature" in err.lower()

    def test_missing_timestamp_fails(self):
        signed = sign_tool_result("tool", {}, "data")
        del signed["_timestamp"]
        ok, err = verify_tool_result(signed)
        assert not ok
        assert "incomplete" in err.lower()

    def test_missing_tool_name_fails(self):
        signed = sign_tool_result("tool", {}, "data")
        del signed["_tool_name"]
        ok, err = verify_tool_result(signed)
        assert not ok

    def test_non_dict_result_fails(self):
        ok, err = verify_tool_result("just a string")
        assert not ok
        assert "not a dict" in err.lower()

    def test_none_result_fails(self):
        ok, err = verify_tool_result(None)
        assert not ok


# ── Tool name mismatch ────────────────────────────────────────────

class TestToolNameMismatch:

    def test_wrong_tool_name_fails(self):
        signed = sign_tool_result(
            tool_name="mcp_run_shell_command",
            arguments={"command": "ls"},
            result="output",
        )
        ok, err = verify_tool_result(signed, tool_name="mcp_fetch")
        assert not ok
        assert "mismatch" in err.lower()

    def test_correct_tool_name_passes(self):
        signed = sign_tool_result(
            tool_name="mcp_fetch",
            arguments={"url": "https://example.com"},
            result="page content",
        )
        ok, err = verify_tool_result(signed, tool_name="mcp_fetch")
        assert ok

    def test_no_expected_tool_name_still_verifies(self):
        """When no expected tool_name is passed, skip name check."""
        signed = sign_tool_result(
            tool_name="any_tool",
            arguments={},
            result="data",
        )
        ok, err = verify_tool_result(signed)  # No tool_name arg
        assert ok


# ── strip_signature_metadata ──────────────────────────────────────

class TestStripMetadata:

    def test_removes_underscore_keys(self):
        signed = sign_tool_result("tool", {}, "data")
        cleaned = strip_signature_metadata(signed)

        assert "_signature" not in cleaned
        assert "_timestamp" not in cleaned
        assert "_tool_name" not in cleaned
        assert "_arguments" not in cleaned
        assert "_conversation_id" not in cleaned

    def test_preserves_content(self):
        signed = sign_tool_result("tool", {}, "data")
        cleaned = strip_signature_metadata(signed)
        assert "content" in cleaned

    def test_non_dict_passthrough(self):
        assert strip_signature_metadata("not a dict") == "not a dict"
        assert strip_signature_metadata(42) == 42
        assert strip_signature_metadata(None) is None


# ── Session secret ─────────────────────────────────────────────────

class TestSessionSecret:

    def test_secret_is_32_bytes(self):
        secret = get_session_secret()
        assert len(secret) == 32

    def test_secret_is_stable_within_session(self):
        a = get_session_secret()
        b = get_session_secret()
        assert a == b

    def test_different_secrets_different_signatures(self):
        """Two sessions with different secrets produce different signatures."""
        import app.mcp.signing as mod

        signed1 = sign_tool_result("tool", {"a": 1}, "data")

        # Reset secret to simulate a new session
        mod._session_secret = None

        signed2 = sign_tool_result("tool", {"a": 1}, "data")

        # Signatures should differ (different secrets)
        assert signed1["_signature"] != signed2["_signature"]

        # Each should only verify against its own secret
        # signed1 was created with the old secret, so it will fail now
        ok, _ = verify_tool_result(signed1)
        assert not ok

        ok2, _ = verify_tool_result(signed2)
        assert ok2
