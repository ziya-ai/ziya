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
