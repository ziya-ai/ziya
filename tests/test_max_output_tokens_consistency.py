"""
Regression tests for issue #4: ZIYA_MAX_OUTPUT_TOKENS has inconsistent defaults.

Every code path that needs a fallback for max_output_tokens must resolve to
the same canonical value (config.DEFAULT_MAX_OUTPUT_TOKENS) when no env var
or model-specific override is present.

Sites covered:
  - models_config.py: DEFAULT_MAX_OUTPUT_TOKENS constant
  - models.py: get_model_settings() fallback, ValueError fallback, initialize_model() base
  - agent.py: astream() env-var chain
  - ziya_bedrock.py: _generate() fallback
  - direct_bedrock.py: request body fallback
  - server.py: continuation threshold, capabilities, ModelSettingsRequest schema
"""

import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure max-output-tokens env vars are unset for each test."""
    monkeypatch.delenv("ZIYA_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("ZIYA_MAX_TOKENS", raising=False)


class TestCanonicalConstant:
    """The constant exists and is a positive integer."""

    def test_constant_exists(self):
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS
        assert isinstance(DEFAULT_MAX_OUTPUT_TOKENS, int)
        assert DEFAULT_MAX_OUTPUT_TOKENS > 0

    def test_constant_matches_endpoint_default(self):
        """The constant should match the bedrock endpoint default."""
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS, ENDPOINT_DEFAULTS
        bedrock_default = ENDPOINT_DEFAULTS["bedrock"]["default_max_output_tokens"]
        assert DEFAULT_MAX_OUTPUT_TOKENS == bedrock_default, (
            f"DEFAULT_MAX_OUTPUT_TOKENS ({DEFAULT_MAX_OUTPUT_TOKENS}) should match "
            f"bedrock endpoint default ({bedrock_default})"
        )


class TestModelsManagerFallback:
    """models.py get_model_settings() uses the canonical default."""

    def test_get_model_settings_fallback_uses_constant(self):
        """When no env var, no default_max_output_tokens in config, fallback
        should be DEFAULT_MAX_OUTPUT_TOKENS, not a hardcoded magic number."""
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS

        # Create a minimal model config with NO default_max_output_tokens
        fake_model_config = {
            "model_id": "test-model",
            "family": "claude",
            # No default_max_output_tokens, no max_output_tokens
        }

        with patch('app.agents.models.ModelManager.get_model_config',
                   return_value=fake_model_config):
            with patch('app.agents.models.ModelManager.ENDPOINT_DEFAULTS', {}):
                from app.agents.models import ModelManager
                settings = ModelManager.get_model_settings()

        # The fallback should be the canonical constant
        assert settings.get("max_output_tokens") == DEFAULT_MAX_OUTPUT_TOKENS, (
            f"Expected {DEFAULT_MAX_OUTPUT_TOKENS}, got {settings.get('max_output_tokens')}"
        )


class TestAgentFallback:
    """agent.py uses the canonical default when env vars are unset."""

    def test_agent_max_tokens_fallback(self):
        """When ZIYA_MAX_OUTPUT_TOKENS and ZIYA_MAX_TOKENS are both unset,
        agent.py should fall back to DEFAULT_MAX_OUTPUT_TOKENS, not None."""
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS

        # Simulate the env-var chain from agent.py
        max_tokens = (
            int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0))
            or int(os.environ.get("ZIYA_MAX_TOKENS", 0))
            or DEFAULT_MAX_OUTPUT_TOKENS
        )
        assert max_tokens == DEFAULT_MAX_OUTPUT_TOKENS
        assert max_tokens is not None, "max_tokens must never be None"

    def test_agent_env_override_honored(self, monkeypatch):
        """Explicit env var should take precedence over the constant."""
        monkeypatch.setenv("ZIYA_MAX_OUTPUT_TOKENS", "8192")
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS

        max_tokens = (
            int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0))
            or int(os.environ.get("ZIYA_MAX_TOKENS", 0))
            or DEFAULT_MAX_OUTPUT_TOKENS
        )
        assert max_tokens == 8192

    def test_agent_legacy_env_honored(self, monkeypatch):
        """Legacy ZIYA_MAX_TOKENS should still work as fallback."""
        monkeypatch.setenv("ZIYA_MAX_TOKENS", "16384")
        from app.config.models_config import DEFAULT_MAX_OUTPUT_TOKENS

        max_tokens = (
            int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0))
            or int(os.environ.get("ZIYA_MAX_TOKENS", 0))
            or DEFAULT_MAX_OUTPUT_TOKENS
        )
        assert max_tokens == 16384


class TestZiyaBedrockFallback:
    """ziya_bedrock.py uses the canonical default, not 32768."""

    def test_no_hardcoded_32768(self):
        """The ziya_bedrock.py fallback should reference the config constant."""
        import inspect
        from app.agents.wrappers.ziya_bedrock import ZiyaBedrock
        source = inspect.getsource(ZiyaBedrock._generate)

        # The old hardcoded 32768 should be gone
        assert "32768" not in source, (
            "ziya_bedrock.py _generate() still contains hardcoded 32768 fallback"
        )


class TestDirectBedrockFallback:
    """direct_bedrock.py uses the canonical default."""

    def test_no_hardcoded_4096_in_body(self):
        """The direct_bedrock.py body construction should not hardcode 4096."""
        import inspect
        from app.agents.direct_bedrock import DirectBedrockHandler
        source = inspect.getsource(DirectBedrockHandler)

        # Check that 4096 is not used as a literal default for max_tokens
        # (it may appear in comments or other contexts, so we check the
        # specific pattern)
        import re
        matches = re.findall(r'settings\.get\(["\']max_output_tokens["\'],\s*4096\)', source)
        assert len(matches) == 0, (
            "direct_bedrock.py still has settings.get('max_output_tokens', 4096)"
        )


class TestAllSitesAgree:
    """Meta-test: verify no stale hardcoded fallbacks remain in key files."""

    @pytest.mark.parametrize("module_path", [
        "app/agents/models.py",
        "app/agents/agent.py",
        "app/agents/wrappers/ziya_bedrock.py",
        "app/agents/direct_bedrock.py",
    ])
    def test_no_hardcoded_max_tokens_defaults(self, module_path):
        """Key files should not contain hardcoded max_output_tokens fallbacks.
        
        Allowed patterns: DEFAULT_MAX_OUTPUT_TOKENS, config.DEFAULT_MAX_OUTPUT_TOKENS
        Forbidden patterns: literal 4096 or 32768 as fallback defaults in
        get() calls or env var chains for max_output_tokens/max_tokens.
        """
        import re
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        filepath = os.path.join(project_root, module_path)

        with open(filepath, 'r') as f:
            content = f.read()

        # Pattern: .get("max_output_tokens", <number>) or
        #          .get("ZIYA_MAX_OUTPUT_TOKENS", <number>)
        # where <number> is a literal integer (not a variable reference)
        forbidden = re.findall(
            r'\.get\(\s*["\'](?:max_output_tokens|ZIYA_MAX_OUTPUT_TOKENS)["\'],\s*(\d+)\s*\)',
            content
        )
        # Filter out legitimate uses (e.g., in comments, or 0 used as sentinel)
        real_violations = [n for n in forbidden if n not in ('0',)]

        assert len(real_violations) == 0, (
            f"{module_path} has hardcoded max_output_tokens defaults: {real_violations}. "
            f"Use config.DEFAULT_MAX_OUTPUT_TOKENS instead."
        )
