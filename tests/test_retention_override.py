"""
Tests for ZIYA_RETENTION_OVERRIDE_DAYS environment variable.

Verifies that:
  - The env var raises plugin-resolved TTLs to the specified minimum
  - TTLs already longer than the override are left untouched
  - Invalid values are ignored gracefully
  - The override reason appears in policy_reason
"""

import os
from datetime import timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.plugins.interfaces import DataRetentionPolicy


def _make_short_policy():
    """Simulate a corporate plugin that sets aggressive 1-day TTLs."""
    return DataRetentionPolicy(
        conversation_data_ttl=timedelta(days=1),
        context_cache_ttl=timedelta(hours=4),
        default_ttl=timedelta(days=1),
        policy_reason="corporate: 1-day retention",
    )


def _make_long_policy():
    """Simulate a plugin with a generous 365-day TTL."""
    return DataRetentionPolicy(
        conversation_data_ttl=timedelta(days=365),
        policy_reason="generous policy",
    )


def _mock_provider(policy: DataRetentionPolicy):
    """Create a mock DataRetentionProvider returning the given policy."""
    provider = MagicMock()
    provider.should_apply.return_value = True
    provider.get_retention_policy.return_value = policy
    provider.provider_id = "test-provider"
    return provider


class TestRetentionOverride:

    def test_override_raises_short_ttls(self):
        """ZIYA_RETENTION_OVERRIDE_DAYS=30 should raise 1-day TTLs to 30 days."""
        provider = _mock_provider(_make_short_policy())

        with patch("app.plugins._data_retention_providers", [provider]):
            with patch.dict(os.environ, {"ZIYA_RETENTION_OVERRIDE_DAYS": "30"}):
                from app.plugins import get_effective_retention_policy
                policy = get_effective_retention_policy()

        assert policy.conversation_data_ttl == timedelta(days=30)
        assert policy.context_cache_ttl == timedelta(days=30)
        assert policy.default_ttl == timedelta(days=30)
        assert "ZIYA_RETENTION_OVERRIDE_DAYS" in policy.policy_reason

    def test_override_does_not_lower_long_ttls(self):
        """Override of 30 days should not shorten a 365-day TTL."""
        provider = _mock_provider(_make_long_policy())

        with patch("app.plugins._data_retention_providers", [provider]):
            with patch.dict(os.environ, {"ZIYA_RETENTION_OVERRIDE_DAYS": "30"}):
                from app.plugins import get_effective_retention_policy
                policy = get_effective_retention_policy()

        assert policy.conversation_data_ttl == timedelta(days=365)

    def test_no_override_when_unset(self):
        """Without the env var, plugin TTLs are used as-is."""
        provider = _mock_provider(_make_short_policy())

        with patch("app.plugins._data_retention_providers", [provider]):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ZIYA_RETENTION_OVERRIDE_DAYS", None)
                from app.plugins import get_effective_retention_policy
                policy = get_effective_retention_policy()

        assert policy.conversation_data_ttl == timedelta(days=1)
        assert "ZIYA_RETENTION_OVERRIDE_DAYS" not in policy.policy_reason

    def test_override_zero_is_noop(self):
        """ZIYA_RETENTION_OVERRIDE_DAYS=0 should be treated as disabled."""
        provider = _mock_provider(_make_short_policy())

        with patch("app.plugins._data_retention_providers", [provider]):
            with patch.dict(os.environ, {"ZIYA_RETENTION_OVERRIDE_DAYS": "0"}):
                from app.plugins import get_effective_retention_policy
                policy = get_effective_retention_policy()

        assert policy.conversation_data_ttl == timedelta(days=1)

    def test_invalid_value_ignored(self):
        """Non-numeric values should be ignored with a warning, not crash."""
        provider = _mock_provider(_make_short_policy())

        with patch("app.plugins._data_retention_providers", [provider]):
            with patch.dict(os.environ, {"ZIYA_RETENTION_OVERRIDE_DAYS": "forever"}):
                from app.plugins import get_effective_retention_policy
                policy = get_effective_retention_policy()

        # Should fall through to the plugin's 1-day TTL
        assert policy.conversation_data_ttl == timedelta(days=1)

    def test_fractional_days(self):
        """Fractional days like 0.5 (12 hours) should work."""
        provider = _mock_provider(_make_short_policy())

        with patch("app.plugins._data_retention_providers", [provider]):
            with patch.dict(os.environ, {"ZIYA_RETENTION_OVERRIDE_DAYS": "0.5"}):
                from app.plugins import get_effective_retention_policy
                policy = get_effective_retention_policy()

        # 0.5 days = 12 hours, which is longer than 4-hour context_cache_ttl
        assert policy.context_cache_ttl == timedelta(hours=12)
        # But 0.5 days is shorter than 1-day conversation_data_ttl — no change
        assert policy.conversation_data_ttl == timedelta(days=1)
