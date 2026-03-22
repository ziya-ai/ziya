"""
Tests that cli.py:setup_env() propagates ALL common_args flags to environment
variables, matching the behaviour of main.py:setup_environment().

Regression for: "CLI silently drops --temperature, --thinking-level, and
5 other parsed flags."

After the setup_environment() consolidation (issue #3), setup_env() now
delegates to the shared setup_environment() which performs endpoint/model
validation.  Tests must supply a valid endpoint (defaults to "bedrock").
"""

import argparse
import os
import pytest
from unittest.mock import patch


def _make_args(**overrides):
    """Build a minimal argparse.Namespace mirroring common_args output."""
    defaults = dict(
        command='ask',
        files=[],
        question='hello',
        model=None,
        profile=None,
        region=None,
        endpoint="bedrock",  # Must be valid — shared setup validates
        root=None,
        no_stream=True,
        debug=False,
        # Model parameter flags (the ones that were missing)
        temperature=None,
        top_p=None,
        top_k=None,
        max_output_tokens=None,
        thinking_level=None,
        model_id=None,
        # File flags
        include=[],
        exclude=[],
        include_only=[],
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Prevent tests from leaking env vars."""
    monkeypatch.setenv("ZIYA_MODE", "chat")
    monkeypatch.setenv("ZIYA_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    # Clear any pre-existing values so we can assert setup_env sets them
    for var in [
        "ZIYA_TEMPERATURE", "ZIYA_TOP_P", "ZIYA_TOP_K",
        "ZIYA_MAX_OUTPUT_TOKENS", "ZIYA_THINKING_LEVEL",
        "ZIYA_MODEL_ID_OVERRIDE",
        "ZIYA_ADDITIONAL_EXCLUDE_DIRS", "ZIYA_INCLUDE_ONLY_DIRS",
        "ZIYA_INCLUDE_DIRS",
    ]:
        monkeypatch.delenv(var, raising=False)


class TestSetupEnvModelParams:
    """Verify model-parameter flags are propagated to env vars."""

    def test_temperature(self):
        from app.cli import setup_env
        setup_env(_make_args(temperature=0.7))
        assert os.environ["ZIYA_TEMPERATURE"] == "0.7"

    def test_top_p(self):
        from app.cli import setup_env
        setup_env(_make_args(top_p=0.9))
        assert os.environ["ZIYA_TOP_P"] == "0.9"

    def test_top_k(self):
        from app.cli import setup_env
        setup_env(_make_args(top_k=40))
        assert os.environ["ZIYA_TOP_K"] == "40"

    def test_max_output_tokens(self):
        from app.cli import setup_env
        setup_env(_make_args(max_output_tokens=8192))
        assert os.environ["ZIYA_MAX_OUTPUT_TOKENS"] == "8192"

    def test_thinking_level(self):
        from app.cli import setup_env
        setup_env(_make_args(thinking_level="high"))
        assert os.environ["ZIYA_THINKING_LEVEL"] == "high"

    def test_model_id(self):
        from app.cli import setup_env
        setup_env(_make_args(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0"))
        assert os.environ["ZIYA_MODEL_ID_OVERRIDE"] == "us.anthropic.claude-sonnet-4-20250514-v1:0"


class TestSetupEnvFileFlags:
    """Verify file inclusion/exclusion flags are propagated."""

    def test_exclude(self):
        from app.cli import setup_env
        setup_env(_make_args(exclude=["node_modules", "dist"]))
        assert os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] == "node_modules,dist"

    def test_include_only(self):
        from app.cli import setup_env
        setup_env(_make_args(include_only=["src", "lib"]))
        assert os.environ["ZIYA_INCLUDE_ONLY_DIRS"] == "src,lib"

    def test_include(self):
        from app.cli import setup_env
        setup_env(_make_args(include=["/external/api"]))
        assert os.environ["ZIYA_INCLUDE_DIRS"] == "/external/api"


class TestSetupEnvDefaultsUnset:
    """Verify that None defaults don't pollute the environment."""

    def test_none_temperature_not_set(self):
        from app.cli import setup_env
        setup_env(_make_args())  # all defaults = None
        assert "ZIYA_TEMPERATURE" not in os.environ

    def test_none_thinking_level_not_set(self):
        from app.cli import setup_env
        setup_env(_make_args())
        assert "ZIYA_THINKING_LEVEL" not in os.environ

    def test_none_model_id_not_set(self):
        from app.cli import setup_env
        setup_env(_make_args())
        assert "ZIYA_MODEL_ID_OVERRIDE" not in os.environ

    def test_empty_exclude_not_set(self):
        from app.cli import setup_env
        setup_env(_make_args(exclude=[]))
        assert "ZIYA_ADDITIONAL_EXCLUDE_DIRS" not in os.environ


class TestSetupEnvExistingFlags:
    """Verify the flags that already worked continue to work."""

    def test_model(self):
        from app.cli import setup_env
        # Use a model that's valid for bedrock
        import app.config.models_config as config
        model = next(iter(config.MODEL_CONFIGS.get("bedrock", {})), None)
        if model:
            setup_env(_make_args(model=model))
            assert os.environ["ZIYA_MODEL"] == model

    def test_endpoint(self):
        from app.cli import setup_env
        # google endpoint requires GOOGLE_API_KEY or similar — just check
        # that the env var is set to "google" by mocking validation
        with patch('app.config.environment.validate_model_and_endpoint',
                   return_value=(True, None, "google")):
            setup_env(_make_args(endpoint="google"))
        assert os.environ["ZIYA_ENDPOINT"] == "google"

    def test_profile(self):
        from app.cli import setup_env
        setup_env(_make_args(profile="my-profile"))
        assert os.environ["ZIYA_AWS_PROFILE"] == "my-profile"
        assert os.environ["AWS_PROFILE"] == "my-profile"

    def test_region(self):
        from app.cli import setup_env
        setup_env(_make_args(region="eu-west-1"))
        assert os.environ["AWS_REGION"] == "eu-west-1"
