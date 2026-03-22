"""
Tests for the shared environment setup in app/config/environment.py.

Validates that both server and CLI entry points get consistent behavior
from the single shared setup_environment() function.
"""

import os
import sys
import types
import pytest

# Ensure project root on path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**overrides):
    """Create a minimal args namespace that mimics argparse output."""
    defaults = dict(
        root=None,
        exclude=[],
        include_only=[],
        include=[],
        endpoint="bedrock",
        model=None,
        model_id=None,
        profile=None,
        region=None,
        temperature=None,
        top_p=None,
        top_k=None,
        max_output_tokens=None,
        thinking_level=None,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


# Keys that setup_environment may write — cleaned between tests
_ENV_KEYS = [
    "ZIYA_USER_CODEBASE_DIR",
    "ZIYA_ADDITIONAL_EXCLUDE_DIRS",
    "ZIYA_INCLUDE_ONLY_DIRS",
    "ZIYA_INCLUDE_DIRS",
    "ZIYA_AWS_PROFILE",
    "AWS_PROFILE",
    "AWS_REGION",
    "ZIYA_ENDPOINT",
    "ZIYA_MODEL",
    "ZIYA_TEMPERATURE",
    "ZIYA_TOP_P",
    "ZIYA_TOP_K",
    "ZIYA_MAX_OUTPUT_TOKENS",
    "ZIYA_THINKING_LEVEL",
    "ZIYA_MODEL_ID_OVERRIDE",
    "ZIYA_TEMPLATES_DIR",
]


@pytest.fixture(autouse=True)
def _clean_env():
    """Remove env vars written by setup_environment before each test."""
    saved = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
    yield
    # Restore original state
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRootDirectory:
    """Root directory should always use direct assignment, not setdefault."""

    def test_root_from_args(self, tmp_path):
        from app.config.environment import setup_environment
        args = _make_args(root=str(tmp_path))
        setup_environment(args)
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(tmp_path)

    def test_root_defaults_to_cwd(self):
        from app.config.environment import setup_environment
        args = _make_args()
        setup_environment(args)
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == os.getcwd()

    def test_root_overrides_stale_env(self, tmp_path):
        """Direct assignment means a stale value is replaced (setdefault bug)."""
        from app.config.environment import setup_environment
        os.environ["ZIYA_USER_CODEBASE_DIR"] = "/stale/path"
        args = _make_args(root=str(tmp_path))
        setup_environment(args)
        assert os.environ["ZIYA_USER_CODEBASE_DIR"] == str(tmp_path)


class TestAWSProfile:
    def test_profile_sets_both_env_vars(self):
        from app.config.environment import setup_environment
        args = _make_args(profile="my-profile")
        setup_environment(args)
        assert os.environ["ZIYA_AWS_PROFILE"] == "my-profile"
        assert os.environ["AWS_PROFILE"] == "my-profile"

    def test_google_endpoint_rejects_profile(self):
        from app.config.environment import setup_environment
        args = _make_args(endpoint="google", profile="some-profile")
        with pytest.raises(SystemExit):
            setup_environment(args)


class TestRegionHandling:
    def test_explicit_region(self):
        from app.config.environment import setup_environment
        args = _make_args(region="eu-west-1")
        setup_environment(args)
        assert os.environ["AWS_REGION"] == "eu-west-1"

    def test_model_specific_default_region(self):
        """When no --region given, MODEL_DEFAULT_REGIONS should kick in."""
        from app.config.environment import setup_environment
        import app.config.models_config as config
        # Pick a model that has a default region (if any exist)
        if config.MODEL_DEFAULT_REGIONS:
            model, expected_region = next(iter(config.MODEL_DEFAULT_REGIONS.items()))
            args = _make_args(model=model)
            setup_environment(args)
            assert os.environ["AWS_REGION"] == expected_region

    def test_global_default_region_when_no_explicit(self):
        from app.config.environment import setup_environment
        import app.config.models_config as config
        args = _make_args()  # region=None, model=None
        setup_environment(args)
        assert os.environ["AWS_REGION"] == config.DEFAULT_REGION


class TestEndpointModelValidation:
    def test_invalid_endpoint_exits(self):
        from app.config.environment import setup_environment
        args = _make_args(endpoint="nonexistent_provider")
        with pytest.raises(SystemExit):
            setup_environment(args)

    def test_auto_detects_endpoint_for_model(self):
        """If model belongs to a different endpoint, auto-detect it."""
        from app.config.environment import setup_environment
        import app.config.models_config as config
        # Find a model that lives in a non-default endpoint
        for ep, models in config.MODEL_CONFIGS.items():
            if ep != config.DEFAULT_ENDPOINT:
                model = next(iter(models))
                args = _make_args(model=model)
                # Simulate that --endpoint was NOT explicitly passed
                original_argv = sys.argv
                sys.argv = ["ziya"]
                try:
                    setup_environment(args)
                finally:
                    sys.argv = original_argv
                assert os.environ["ZIYA_ENDPOINT"] == ep
                assert args.endpoint == ep  # should be corrected on the namespace
                break


class TestModelParameters:
    def test_temperature_set(self):
        from app.config.environment import setup_environment
        args = _make_args(temperature=0.7)
        setup_environment(args)
        assert os.environ["ZIYA_TEMPERATURE"] == "0.7"

    def test_model_id_override(self):
        from app.config.environment import setup_environment
        args = _make_args(model_id="arn:aws:bedrock:us-east-1:123:custom/my-model")
        setup_environment(args)
        assert os.environ["ZIYA_MODEL_ID_OVERRIDE"] == "arn:aws:bedrock:us-east-1:123:custom/my-model"

    def test_none_params_not_set(self):
        """Parameters left at None should not appear in env."""
        from app.config.environment import setup_environment
        args = _make_args()
        setup_environment(args)
        assert "ZIYA_TEMPERATURE" not in os.environ
        assert "ZIYA_TOP_P" not in os.environ
        assert "ZIYA_MODEL_ID_OVERRIDE" not in os.environ


class TestTemplatesDir:
    def test_templates_dir_set(self):
        from app.config.environment import setup_environment
        args = _make_args()
        setup_environment(args)
        templates = os.environ.get("ZIYA_TEMPLATES_DIR", "")
        assert templates.endswith("templates")
        assert os.path.isabs(templates)


class TestFileInclusion:
    def test_exclude_dirs(self):
        from app.config.environment import setup_environment
        args = _make_args(exclude=["node_modules", "dist"])
        setup_environment(args)
        assert os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] == "node_modules,dist"

    def test_include_only(self):
        from app.config.environment import setup_environment
        args = _make_args(include_only=["src", "lib"])
        setup_environment(args)
        assert os.environ["ZIYA_INCLUDE_ONLY_DIRS"] == "src,lib"

    def test_include_external(self):
        from app.config.environment import setup_environment
        args = _make_args(include=["/opt/shared"])
        setup_environment(args)
        assert os.environ["ZIYA_INCLUDE_DIRS"] == "/opt/shared"


class TestHelperFunctions:
    def test_find_endpoint_for_model_found(self):
        from app.config.environment import find_endpoint_for_model
        import app.config.models_config as config
        for ep, models in config.MODEL_CONFIGS.items():
            model = next(iter(models))
            assert find_endpoint_for_model(model) == ep
            break

    def test_find_endpoint_for_model_not_found(self):
        from app.config.environment import find_endpoint_for_model
        assert find_endpoint_for_model("nonexistent-model-xyz") is None

    def test_validate_valid_endpoint_model(self):
        from app.config.environment import validate_model_and_endpoint
        import app.config.models_config as config
        ep = next(iter(config.MODEL_CONFIGS))
        model = next(iter(config.MODEL_CONFIGS[ep]))
        is_valid, err, corrected = validate_model_and_endpoint(ep, model)
        assert is_valid
        assert err is None

    def test_validate_invalid_endpoint(self):
        from app.config.environment import validate_model_and_endpoint
        is_valid, err, _ = validate_model_and_endpoint("nonexistent", "some-model")
        assert not is_valid
        assert "Invalid endpoint" in err


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
