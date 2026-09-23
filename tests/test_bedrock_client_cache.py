"""
Tests for app/providers/bedrock_client_cache.py

Verifies that:
1. The cache module is importable without pulling in app.agents.models
2. Config hashing is deterministic
3. Client caching returns the same object for identical configs
4. clear_cache() resets state
"""

import importlib
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestBedrockClientCacheImportIsolation(unittest.TestCase):
    """The cache module must not import from app.agents.*"""

    def test_no_agents_import_at_module_level(self):
        """Importing bedrock_client_cache must not trigger app.agents.models."""
        # Record which modules are loaded before import
        before = set(sys.modules.keys())

        # Force re-import to catch module-level side effects
        mod_name = 'app.providers.bedrock_client_cache'
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        importlib.import_module(mod_name)

        after = set(sys.modules.keys())
        newly_loaded = after - before

        agents_modules = [m for m in newly_loaded if m.startswith('app.agents')]
        self.assertEqual(
            agents_modules, [],
            f"bedrock_client_cache imported agent modules at load time: {agents_modules}"
        )


class TestConfigHash(unittest.TestCase):
    """get_client_config_hash must be deterministic and distinct."""

    def test_deterministic(self):
        from app.providers.bedrock_client_cache import get_client_config_hash
        h1 = get_client_config_hash("profile", "us-west-2", "model-v1")
        h2 = get_client_config_hash("profile", "us-west-2", "model-v1")
        self.assertEqual(h1, h2)

    def test_distinct_for_different_regions(self):
        from app.providers.bedrock_client_cache import get_client_config_hash
        h1 = get_client_config_hash("p", "us-east-1", "m")
        h2 = get_client_config_hash("p", "eu-west-1", "m")
        self.assertNotEqual(h1, h2)


class TestRegionFallback(unittest.TestCase):
    """
    Regression: ModelManager._state['aws_region'] is None until the model has
    been (re)initialized, and the direct streaming path passed that None
    straight into boto3. botocore only consults AWS_DEFAULT_REGION on its own
    (not AWS_REGION, which is what Ziya's startup sets), so a user with no
    region in their profile hit NoRegionError on bedrock-runtime even though
    Ziya had logged "Using AWS region ...". The cache must resolve a missing
    region itself instead of handing None to boto3.
    """

    def setUp(self):
        from app.providers import bedrock_client_cache as bcc
        bcc.clear_cache()

    def _run(self, region_arg, env):
        from app.providers import bedrock_client_cache as bcc
        session = MagicMock()
        sts = MagicMock()
        sts.get_caller_identity.return_value = {"Arn": "arn:test"}
        runtime = MagicMock()

        def _client(service, **kwargs):
            return sts if service == "sts" else runtime
        session.client.side_effect = _client

        with patch.dict(os.environ, env, clear=False), \
             patch("app.utils.aws_utils.create_fresh_boto3_session", return_value=session), \
             patch("app.utils.custom_bedrock.CustomBedrockClient", side_effect=lambda c, model_config=None: c), \
             patch("app.utils.aws_utils.ThrottleSafeBedrock", side_effect=lambda c: c):
            for k in ("AWS_DEFAULT_REGION",):
                os.environ.pop(k, None)
            bcc.get_persistent_bedrock_client(
                aws_profile=None, region=region_arg, model_id="m",
            )
        runtime_calls = [c for c in session.client.call_args_list
                         if c.args and c.args[0] == "bedrock-runtime"]
        self.assertEqual(len(runtime_calls), 1)
        return runtime_calls[0].kwargs.get("region_name")

    def test_none_region_falls_back_to_aws_region_env(self):
        region = self._run(None, {"AWS_REGION": "eu-central-1"})
        self.assertEqual(region, "eu-central-1")

    def test_explicit_region_is_kept(self):
        region = self._run("us-east-1", {"AWS_REGION": "eu-central-1"})
        self.assertEqual(region, "us-east-1")

    def test_none_region_never_reaches_boto3(self):
        env = {k: v for k, v in os.environ.items()}
        env.pop("AWS_REGION", None)
        with patch.dict(os.environ, env, clear=True):
            region = self._run(None, {})
        self.assertIsNotNone(region)


class TestClearCache(unittest.TestCase):
    """clear_cache must reset module-level state."""

    def test_clear_removes_entries(self):
        from app.providers import bedrock_client_cache as bcc
        # Manually insert a fake entry
        bcc._client_cache["fake_hash"] = "fake_client"
        bcc._current_config_hash = "fake_hash"

        bcc.clear_cache()

        self.assertEqual(len(bcc._client_cache), 0)
        self.assertIsNone(bcc._current_config_hash)


if __name__ == '__main__':
    unittest.main()
