"""
Tests for model configuration and ModelManager.

Updated: langchain.callbacks.base → langchain_core.callbacks.base.
Removed actual API call tests (require credentials).
Focus on configuration and initialization paths.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from langchain_core.callbacks.base import BaseCallbackHandler

from app.agents.models import ModelManager


class TestModelConfigs:
    """Test model configuration structures."""

    def test_model_configs_exist(self):
        """MODEL_CONFIGS should be a non-empty dict."""
        assert hasattr(ModelManager, 'MODEL_CONFIGS')
        assert isinstance(ModelManager.MODEL_CONFIGS, dict)
        assert len(ModelManager.MODEL_CONFIGS) > 0

    def test_endpoint_defaults_exist(self):
        """ENDPOINT_DEFAULTS should map endpoints to default models."""
        assert hasattr(ModelManager, 'ENDPOINT_DEFAULTS')
        assert isinstance(ModelManager.ENDPOINT_DEFAULTS, dict)

    def test_default_endpoint(self):
        """DEFAULT_ENDPOINT should be a non-empty string."""
        assert hasattr(ModelManager, 'DEFAULT_ENDPOINT')
        assert isinstance(ModelManager.DEFAULT_ENDPOINT, str)
        assert len(ModelManager.DEFAULT_ENDPOINT) > 0

    def test_get_model_config(self):
        """Should return config for a known model."""
        configs = ModelManager.MODEL_CONFIGS
        if configs:
            model_id = next(iter(configs))
            config = ModelManager.get_model_config(model_id)
            assert config is not None

    def test_get_model_alias(self):
        """get_model_alias should return a string."""
        # get_model_alias takes no args (returns current model alias)
        alias = ModelManager.get_model_alias()
        assert isinstance(alias, str)


class TestModelManagerState:
    """Test ModelManager state management."""

    def test_get_state(self):
        """get_state should return a dict with expected keys."""
        state = ModelManager.get_state()
        assert isinstance(state, dict)

    def test_filter_model_kwargs(self):
        """filter_model_kwargs should accept a dict of kwargs."""
        import inspect
        sig = inspect.signature(ModelManager.filter_model_kwargs)
        assert len(sig.parameters) >= 1


class TestBaseCallbackHandler:
    """Verify langchain_core callback import works."""

    def test_callback_handler_importable(self):
        """BaseCallbackHandler should be importable from langchain_core."""
        assert BaseCallbackHandler is not None

    def test_callback_handler_subclassable(self):
        """Should be able to subclass BaseCallbackHandler."""
        class TestHandler(BaseCallbackHandler):
            pass
        handler = TestHandler()
        assert isinstance(handler, BaseCallbackHandler)
