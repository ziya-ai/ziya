"""Tests for resolve_mantle_region — mantle model availability is per-region
(anthropic.claude-fable-5 exists on bedrock-mantle.us-east-1 but not
us-west-2), so the session region must be clamped to the model's
available_regions or invokes 404 with "model does not exist".
"""

from unittest.mock import patch

from app.providers.bedrock_mantle import resolve_mantle_region

FABLE_CFG = {
    "available_regions": ["us-east-1"],
    "preferred_region": "us-east-1",
}


class TestResolveMantleRegion:
    def test_clamps_unavailable_region_to_preferred(self):
        # The live bug: session resolved to us-west-2, fable is us-east-1-only.
        assert resolve_mantle_region(FABLE_CFG, "us-west-2") == "us-east-1"

    def test_available_region_passes_through(self):
        assert resolve_mantle_region(FABLE_CFG, "us-east-1") == "us-east-1"

    def test_multi_region_config_keeps_matching_region(self):
        cfg = {"available_regions": ["us-east-1", "us-west-2"],
               "preferred_region": "us-east-1"}
        assert resolve_mantle_region(cfg, "us-west-2") == "us-west-2"

    def test_falls_back_to_first_available_without_preferred(self):
        cfg = {"available_regions": ["us-east-2"]}
        assert resolve_mantle_region(cfg, "us-west-2") == "us-east-2"

    def test_no_available_regions_trusts_caller(self):
        assert resolve_mantle_region({}, "us-west-2") == "us-west-2"
        assert resolve_mantle_region(None, "us-west-2") == "us-west-2"

    def test_region_none_reads_env(self):
        with patch.dict("os.environ", {"AWS_REGION": "us-west-2"}):
            # Env region still gets clamped against the model config.
            assert resolve_mantle_region(FABLE_CFG, None) == "us-east-1"

    def test_region_none_no_env_defaults_east(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("AWS_REGION", None)
            env.pop("AWS_DEFAULT_REGION", None)
            assert resolve_mantle_region({}, None) == "us-east-1"

    def test_empty_available_regions_list_trusts_caller(self):
        cfg = {"available_regions": [], "preferred_region": "us-east-1"}
        assert resolve_mantle_region(cfg, "us-west-2") == "us-west-2"


class TestFactoryMantleConstruction:
    """The factory must forward aws_profile to BedrockMantleProvider —
    dropping it made the SigV4 transport sign with the default credential
    chain (potentially a different account with the wrong retention mode).
    """

    def test_factory_passes_profile_and_clamped_region(self):
        from app.providers import factory
        captured = {}

        class FakeProvider:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch("app.providers.bedrock_mantle.BedrockMantleProvider", FakeProvider):
            provider = factory.create_provider(
                endpoint="bedrock",
                model_id="anthropic.claude-fable-5",
                model_config={"endpoint_override": "bedrock-mantle",
                              "available_regions": ["us-east-1"],
                              "preferred_region": "us-east-1"},
                aws_profile="ziya",
                region="us-west-2",
            )
        assert captured["aws_profile"] == "ziya"
        assert captured["region"] == "us-east-1"
