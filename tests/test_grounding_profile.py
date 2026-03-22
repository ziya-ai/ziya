"""
Tests for GroundingService AWS profile resolution.

Ensures ZIYA_AWS_PROFILE takes precedence over AWS_PROFILE,
consistent with every other module in the codebase.
"""

import os
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove profile env vars before each test."""
    monkeypatch.delenv("ZIYA_AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def _make_service(profile_name=None):
    """Instantiate GroundingService with boto3 mocked out."""
    with patch("app.services.grounding.boto3") as mock_boto:
        mock_session = MagicMock()
        mock_boto.Session.return_value = mock_session
        mock_session.client.return_value = MagicMock()

        from app.services.grounding import GroundingService
        svc = GroundingService(profile_name=profile_name)
        return mock_boto, svc


class TestGroundingProfilePrecedence:
    """ZIYA_AWS_PROFILE must win over AWS_PROFILE."""

    def test_explicit_profile_name_wins(self, monkeypatch):
        monkeypatch.setenv("ZIYA_AWS_PROFILE", "env-ziya")
        monkeypatch.setenv("AWS_PROFILE", "env-aws")
        mock_boto, _ = _make_service(profile_name="explicit")
        mock_boto.Session.assert_called_once_with(profile_name="explicit")

    def test_ziya_profile_over_aws_profile(self, monkeypatch):
        monkeypatch.setenv("ZIYA_AWS_PROFILE", "ziya-wins")
        monkeypatch.setenv("AWS_PROFILE", "aws-loses")
        mock_boto, _ = _make_service()
        mock_boto.Session.assert_called_once_with(profile_name="ziya-wins")

    def test_aws_profile_used_when_ziya_absent(self, monkeypatch):
        monkeypatch.setenv("AWS_PROFILE", "fallback-aws")
        mock_boto, _ = _make_service()
        mock_boto.Session.assert_called_once_with(profile_name="fallback-aws")

    def test_default_ziya_when_no_env(self):
        mock_boto, _ = _make_service()
        mock_boto.Session.assert_called_once_with(profile_name="ziya")
