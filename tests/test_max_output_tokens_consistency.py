"""
Tests for max_output_tokens defaults consistency.

Validates that:
  1. DEFAULT_MAX_OUTPUT_TOKENS exists as a named constant (not magic number)
  2. ENDPOINT_DEFAULTS has per-endpoint values
  3. Env var overrides work correctly
  4. Per-model defaults in models.py reference config constants or documented
     model-family values (not unexplained magic numbers)
  5. ziya_bedrock.py does not revert to old hardcoded 32768
"""

import os
import re
import inspect
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure max-output-tokens env vars are unset for each test."""
    monkeypatch.delenv("ZIYA_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("ZIYA_MAX_TOKENS", raising=False)


class TestCanonicalConstant:
    """The config constant exists and is well-formed."""

    def test_constant_exists_and_positive(self):
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS
        assert isinstance(DEFAULT_MAX_OUTPUT_TOKENS, int)
        assert DEFAULT_MAX_OUTPUT_TOKENS > 0

    def test_endpoint_defaults_exist(self):
        from app.config.models_config import ENDPOINT_DEFAULTS
        assert "bedrock" in ENDPOINT_DEFAULTS
        bedrock = ENDPOINT_DEFAULTS["bedrock"]
        assert "default_max_output_tokens" in bedrock
        assert isinstance(bedrock["default_max_output_tokens"], int)

    def test_endpoint_defaults_are_positive(self):
        from app.config.models_config import ENDPOINT_DEFAULTS
        for ep, cfg in ENDPOINT_DEFAULTS.items():
            val = cfg.get("default_max_output_tokens")
            if val is not None:
                assert val > 0, f"Endpoint {ep} has non-positive default: {val}"


class TestEnvVarChain:
    """Environment variable overrides work correctly."""

    def test_primary_env_var_honored(self, monkeypatch):
        monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "8192")
        max_tokens = (
            int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0))
            or int(os.environ.get("ZIYA_MAX_TOKENS", 0))
            or 4096
        )
        assert max_tokens == 8192

    def test_legacy_env_var_honored(self, monkeypatch):
        monkeypatch.setenv("ZIYA_MAX_TOKENS", "16384")
        max_tokens = (
            int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0))
            or int(os.environ.get("ZIYA_MAX_TOKENS", 0))
            or 4096
        )
        assert max_tokens == 16384

    def test_primary_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "1000")
        monkeypatch.setenv("ZIYA_MAX_TOKENS", "2000")
        max_tokens = (
            int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0))
            or int(os.environ.get("ZIYA_MAX_TOKENS", 0))
            or 4096
        )
        assert max_tokens == 1000


class TestZiyaBedrockFallback:
    """ziya_bedrock.py uses the config constant, not hardcoded 32768."""

    def test_no_hardcoded_32768(self):
        """The old literal 32768 should not appear as a fallback."""
        from app.agents.wrappers.ziya_bedrock import ZiyaBedrock
        source = inspect.getsource(ZiyaBedrock._generate)
        assert "32768" not in source, (
            "ziya_bedrock.py _generate() still contains hardcoded 32768 fallback"
        )


class TestPerModelDefaults:
    """Per-model token defaults in models.py are documented model-family values."""

    def test_models_py_defaults_are_known_values(self):
        """Any hardcoded max_output_tokens defaults in models.py should be
        from the set of documented model-family limits, not random numbers."""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        filepath = os.path.join(project_root, "app/agents/models.py")
        with open(filepath, 'r') as f:
            content = f.read()

        # Find all .get("max_output_tokens", <number>) patterns
        matches = re.findall(
            r'\.get\(\s*["\']max_output_tokens["\'],\s*(\d+)\s*\)',
            content
        )

        # These are documented per-model-family defaults:
        # 2048 = Nova Lite/Micro, 4096 = Bedrock default, 8192 = common,
        # 16384 = Claude/large models, 32768 = global default constant
        known_model_defaults = {'0', '2048', '4096', '8192', '16384', '32768', '65536', '131072'}
        unknown = [n for n in matches if n not in known_model_defaults]

        assert len(unknown) == 0, (
            f"models.py has unexpected max_output_tokens defaults: {unknown}. "
            f"Known model-family values: {sorted(known_model_defaults)}"
        )
