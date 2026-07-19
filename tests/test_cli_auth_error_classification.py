"""
Tests for app.cli._check_auth_quick / _print_auth_error.

Regression coverage for a real incident: during an AWS-side outage (STS
unreachable due to a network/service issue, not expired credentials), the
CLI told the user to run `aws sso login` — the wrong remediation, since
re-authenticating does nothing to fix a network timeout.

_check_auth_quick's contract changed from `bool` to `(valid, message)`,
threading check_aws_credentials()'s diagnostic message through so
_print_auth_error can distinguish "credentials missing/expired" from
"could not reach AWS" and print the right guidance for each.
"""

import os
import sys
from unittest.mock import patch

import pytest

from app.cli import _check_auth_quick, _print_auth_error


# ---------------------------------------------------------------------------
# _check_auth_quick
# ---------------------------------------------------------------------------

class TestCheckAuthQuick:
    """Every endpoint branch must return a (bool, message_or_None) tuple."""

    def test_bedrock_valid_credentials(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        with patch("app.utils.aws_utils.check_aws_credentials", return_value=(True, None)):
            result = _check_auth_quick(profile="default")
        assert result == (True, None)

    def test_bedrock_expired_credentials_message_passthrough(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        msg = "⚠️ AWS CREDENTIALS ERROR: Invalid AWS credentials. Please check your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        with patch("app.utils.aws_utils.check_aws_credentials", return_value=(False, msg)):
            valid, message = _check_auth_quick(profile=None)
        assert valid is False
        assert message == msg

    def test_bedrock_network_error_message_passthrough(self, monkeypatch):
        """The actual bug scenario: STS unreachable, not a credential problem."""
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        msg = (
            "⚠️ NETWORK ERROR: Could not reach the credentials provider.\n\n"
            "  Detail: Read timed out.\n\n"
            "If you are on a corporate network, check that your VPN is connected."
        )
        with patch("app.utils.aws_utils.check_aws_credentials", return_value=(False, msg)):
            valid, message = _check_auth_quick(profile=None)
        assert valid is False
        assert "NETWORK ERROR" in message

    def test_bedrock_import_error_returns_none_message(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        with patch("app.utils.aws_utils.check_aws_credentials", side_effect=ImportError("boom")):
            result = _check_auth_quick(profile=None)
        assert result == (False, None)

    def test_google_endpoint_key_present(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        assert _check_auth_quick() == (True, None)

    def test_google_endpoint_key_missing(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "google")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert _check_auth_quick() == (False, None)

    def test_openai_endpoint_key_present(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        assert _check_auth_quick() == (True, None)

    def test_openai_endpoint_base_url_present(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
        assert _check_auth_quick() == (True, None)

    def test_openai_endpoint_missing_both(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        assert _check_auth_quick() == (False, None)

    def test_anthropic_endpoint_key_present(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        assert _check_auth_quick() == (True, None)

    def test_anthropic_endpoint_key_missing(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _check_auth_quick() == (False, None)

    def test_unknown_endpoint_defaults_true(self, monkeypatch):
        monkeypatch.setenv("ZIYA_ENDPOINT", "some_future_endpoint")
        assert _check_auth_quick() == (True, None)


# ---------------------------------------------------------------------------
# _print_auth_error
# ---------------------------------------------------------------------------

class TestPrintAuthError:
    """Verify the printed guidance matches the failure's actual cause."""

    def test_bedrock_generic_failure_suggests_reauth(self, monkeypatch, capsys):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        _print_auth_error(None)
        err = capsys.readouterr().err
        assert "credentials are missing or expired" in err
        assert "aws sso login" in err

    def test_bedrock_credential_message_suggests_reauth(self, monkeypatch, capsys):
        """A genuine credential-error message (no NETWORK ERROR marker) still
        gets the standard re-authenticate guidance."""
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        _print_auth_error("⚠️ AWS CREDENTIALS ERROR: Invalid AWS credentials.")
        err = capsys.readouterr().err
        assert "aws sso login" in err
        assert "network" not in err.lower()

    def test_bedrock_network_error_does_not_suggest_reauth(self, monkeypatch, capsys):
        """Regression: previously this branch always printed 'aws sso login'
        guidance even when the real cause was an unreachable AWS endpoint."""
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        network_msg = "⚠️ NETWORK ERROR: Could not reach the credentials provider.\n\n  Detail: Read timed out."
        _print_auth_error(network_msg)
        err = capsys.readouterr().err
        assert "aws sso login" not in err
        assert "network" in err.lower() or "service issue" in err.lower()
        # The original diagnostic detail must still be visible for troubleshooting.
        assert "Read timed out" in err

    def test_google_endpoint_ignores_message(self, monkeypatch, capsys):
        """Non-bedrock endpoints don't have a network-vs-credential distinction
        wired up (check_aws_credentials is bedrock-specific) — message is unused."""
        monkeypatch.setenv("ZIYA_ENDPOINT", "google")
        _print_auth_error("⚠️ NETWORK ERROR: irrelevant")
        err = capsys.readouterr().err
        assert "GOOGLE_API_KEY" in err

    def test_always_prints_auth_failed_header(self, monkeypatch, capsys):
        monkeypatch.setenv("ZIYA_ENDPOINT", "bedrock")
        _print_auth_error(None)
        err = capsys.readouterr().err
        assert "Authentication failed" in err
