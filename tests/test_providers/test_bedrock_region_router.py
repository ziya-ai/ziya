"""Tests for BedrockRegionRouter — multi-region weighted routing."""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.providers.bedrock_region_router import (
    BedrockRegionRouter,
    RegionEndpoint,
    _DEFAULT_BASE_WEIGHT,
    _MIN_WEIGHT_FRACTION,
    _PRIMARY_REGION_BONUS,
    _THROTTLE_COOLDOWN_SECS,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture
def multi_region_config():
    """Model config with US + EU model IDs (like sonnet4.0)."""
    return {
        "model_id": {
            "us": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "eu": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
        },
        "available_regions": [
            "us-east-1", "us-west-2", "eu-central-1", "eu-west-1",
        ],
        "preferred_region": "us-east-1",
    }


@pytest.fixture
def single_region_config():
    """Model config with a single string model_id (like sonnet3.7)."""
    return {
        "model_id": "eu.anthropic.claude-3-7-sonnet-20250219-v1:0",
        "available_regions": ["eu-west-1", "eu-central-1"],
        "region_restricted": True,
    }


@pytest.fixture
def single_prefix_config():
    """Model config with only US prefix (like opus4)."""
    return {
        "model_id": {"us": "us.anthropic.claude-opus-4-20250514-v1:0"},
        "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
    }


# -----------------------------------------------------------------------
# Router construction
# -----------------------------------------------------------------------

class TestRouterConstruction:
    def test_multi_region_enabled(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        assert router.enabled is True
        assert len(router.regions) == 2

    def test_single_model_id_string_disabled(self, single_region_config):
        router = BedrockRegionRouter(single_region_config, "ziya", "eu-west-1")
        assert router.enabled is False
        assert len(router.regions) == 1

    def test_single_prefix_dict_disabled(self, single_prefix_config):
        router = BedrockRegionRouter(single_prefix_config, "ziya", "us-east-1")
        assert router.enabled is False
        assert len(router.regions) == 1

    def test_primary_region_gets_bonus(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        us_ep = router.get_endpoint("us-east-1")
        eu_ep = [ep for r, ep in router.status()["endpoints"].items() if r != "us-east-1"]
        assert us_ep is not None
        assert us_ep.base_weight == _DEFAULT_BASE_WEIGHT * _PRIMARY_REGION_BONUS
        assert eu_ep[0]["base_weight"] == _DEFAULT_BASE_WEIGHT

    def test_empty_model_config(self):
        router = BedrockRegionRouter({}, "ziya", "us-east-1")
        assert router.enabled is False

    def test_empty_model_id_dict(self):
        router = BedrockRegionRouter({"model_id": {}}, "ziya", "us-east-1")
        assert router.enabled is False


# -----------------------------------------------------------------------
# Endpoint selection
# -----------------------------------------------------------------------

class TestEndpointSelection:
    def test_select_returns_endpoint(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.select_endpoint()
        assert ep is not None
        assert ep.region in router.regions

    def test_select_with_exclude(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.select_endpoint(exclude="us-east-1")
        assert ep is not None
        assert ep.region != "us-east-1"

    def test_select_all_excluded_returns_none(self, multi_region_config):
        """When all regions are excluded, returns None."""
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        # Exclude both regions
        regions = router.regions
        # Only one exclude param, but if we have 2 regions and exclude one,
        # we should still get the other
        ep = router.select_endpoint(exclude=regions[0])
        assert ep is not None
        assert ep.region == regions[1]

    def test_select_disabled_router(self, single_region_config):
        router = BedrockRegionRouter(single_region_config, "ziya", "eu-west-1")
        ep = router.select_endpoint()
        assert ep is not None
        assert ep.region == "eu-west-1"

    def test_weighted_selection_favors_primary(self, multi_region_config):
        """Primary region (with bonus) should be selected more often."""
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        counts = {"us-east-1": 0}
        trials = 1000
        for _ in range(trials):
            ep = router.select_endpoint()
            counts[ep.region] = counts.get(ep.region, 0) + 1

        # Primary should win > 50% (with 1.5x bonus, expected ~60%)
        assert counts.get("us-east-1", 0) > trials * 0.45

    def test_throttled_region_selected_less(self, multi_region_config):
        """After throttle, the penalized region should be selected less."""
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")

        # Heavily throttle the primary
        for _ in range(5):
            router.report_throttle("us-east-1")

        counts = {"us-east-1": 0}
        trials = 1000
        for _ in range(trials):
            ep = router.select_endpoint()
            counts[ep.region] = counts.get(ep.region, 0) + 1

        # Throttled primary should be selected much less than the alternative
        non_primary = [r for r in router.regions if r != "us-east-1"]
        alt_count = sum(counts.get(r, 0) for r in non_primary)
        assert alt_count > counts.get("us-east-1", 0)


# -----------------------------------------------------------------------
# Throttle tracking and weight recovery
# -----------------------------------------------------------------------

class TestThrottleTracking:
    def test_report_throttle_reduces_weight(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.get_endpoint("us-east-1")
        original_weight = ep.effective_weight

        router.report_throttle("us-east-1")
        assert ep.effective_weight < original_weight
        assert ep.throttle_count == 1

    def test_multiple_throttles_increase_penalty(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.get_endpoint("us-east-1")

        router.report_throttle("us-east-1")
        w1 = ep.effective_weight

        router.report_throttle("us-east-1")
        w2 = ep.effective_weight

        assert w2 < w1

    def test_weight_never_below_minimum(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.get_endpoint("us-east-1")

        # Throttle many times
        for _ in range(20):
            router.report_throttle("us-east-1")

        min_allowed = ep.base_weight * _MIN_WEIGHT_FRACTION
        assert ep.effective_weight >= min_allowed

    def test_weight_recovers_after_cooldown(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.get_endpoint("us-east-1")

        router.report_throttle("us-east-1")
        # Simulate cooldown elapsed
        ep.last_throttle_time = time.monotonic() - _THROTTLE_COOLDOWN_SECS - 1
        assert ep.effective_weight == ep.base_weight

    def test_partial_recovery(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.get_endpoint("us-east-1")

        router.report_throttle("us-east-1")
        penalized = ep.effective_weight

        # Simulate half cooldown
        ep.last_throttle_time = time.monotonic() - _THROTTLE_COOLDOWN_SECS / 2
        half_recovered = ep.effective_weight

        assert penalized < half_recovered < ep.base_weight

    def test_report_throttle_unknown_region_noop(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        router.report_throttle("mars-west-1")  # should not raise


# -----------------------------------------------------------------------
# Success tracking
# -----------------------------------------------------------------------

class TestSuccessTracking:
    def test_success_decays_throttle_count(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        ep = router.get_endpoint("us-east-1")

        router.report_throttle("us-east-1")
        router.report_throttle("us-east-1")
        assert ep.throttle_count == 2

        # 3 successes should decay throttle_count by 1
        for _ in range(3):
            router.report_success("us-east-1")
        assert ep.throttle_count == 1

    def test_success_unknown_region_noop(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        router.report_success("mars-west-1")  # should not raise


# -----------------------------------------------------------------------
# Client creation
# -----------------------------------------------------------------------

class TestClientCreation:
    @patch("app.agents.models.ModelManager._get_persistent_bedrock_client")
    def test_get_client_caches(self, mock_get_client, multi_region_config):
        """Client should be created once and cached."""
        mock_get_client.return_value = MagicMock()
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")

        client1 = router.get_client_for_region("us-east-1")
        client2 = router.get_client_for_region("us-east-1")

        assert client1 is client2
        assert mock_get_client.call_count == 1

    def test_get_client_unknown_region_returns_none(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        assert router.get_client_for_region("mars-west-1") is None

    @patch("app.agents.models.ModelManager._get_persistent_bedrock_client")
    def test_get_client_failure_returns_none(self, mock_get_client, multi_region_config):
        mock_get_client.side_effect = Exception("creds failed")
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        assert router.get_client_for_region("us-east-1") is None


# -----------------------------------------------------------------------
# Status / diagnostics
# -----------------------------------------------------------------------

class TestStatus:
    def test_status_structure(self, multi_region_config):
        router = BedrockRegionRouter(multi_region_config, "ziya", "us-east-1")
        status = router.status()

        assert status["enabled"] is True
        assert status["primary_region"] == "us-east-1"
        assert "endpoints" in status
        for region, info in status["endpoints"].items():
            assert "model_id" in info
            assert "base_weight" in info
            assert "effective_weight" in info
            assert "throttle_count" in info
            assert "success_count" in info


# -----------------------------------------------------------------------
# Region prefix resolution
# -----------------------------------------------------------------------

class TestPrefixResolution:
    def test_us_prefix_picks_primary_if_matching(self):
        region = BedrockRegionRouter._pick_region_for_prefix(
            "us", ["us-east-1", "us-west-2"], "us-east-1",
        )
        assert region == "us-east-1"

    def test_eu_prefix_picks_available(self):
        region = BedrockRegionRouter._pick_region_for_prefix(
            "eu", ["eu-central-1"], "us-east-1",
        )
        assert region == "eu-central-1"

    def test_unknown_prefix_falls_back_to_primary(self):
        region = BedrockRegionRouter._pick_region_for_prefix(
            "xyz", [], "us-east-1",
        )
        assert region == "us-east-1"

    def test_global_prefix(self):
        region = BedrockRegionRouter._pick_region_for_prefix(
            "global", ["us-east-1"], "us-east-1",
        )
        assert region == "us-east-1"
