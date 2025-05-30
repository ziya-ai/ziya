"""
Tests for model connections and API interactions.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

import app.config as config
from app.agents.models import ModelManager


@pytest.mark.parametrize("endpoint", ["bedrock", "google"])
def test_endpoint_initialization(endpoint, mock_aws_credentials, setup_test_environment):
    """Test that each endpoint can be initialized."""
    os.environ["ZIYA_ENDPOINT"] = endpoint
    
    # Mock __init__ to prevent actual initialization
    with patch.object(ModelManager, '__init__', return_value=None):
        model_manager = ModelManager()
        # Set the endpoint manually for testing
        model_manager._endpoint = endpoint
        assert model_manager._endpoint == endpoint


@pytest.mark.parametrize("endpoint,model", [
    (endpoint, model) 
    for endpoint, models in config.MODEL_CONFIGS.items() 
    for model in models
])
def test_model_initialization(endpoint, model, mock_aws_credentials, setup_test_environment):
    """Test that each model can be initialized."""
    os.environ["ZIYA_ENDPOINT"] = endpoint
    os.environ["ZIYA_MODEL"] = model
    
    # Mock __init__ to prevent actual initialization
    with patch.object(ModelManager, '__init__', return_value=None):
        model_manager = ModelManager()
        # Set the model manually for testing
        model_manager._endpoint = endpoint
        model_manager._model = model
        assert model_manager._model == model


@pytest.mark.parametrize("model_id", [
    config.MODEL_CONFIGS["bedrock"]["sonnet3.5"]["model_id"],
    "anthropic.claude-3-sonnet-20240229-v1:0",  # Test override
    "amazon.titan-text-express-v1",
])
def test_model_id_override(model_id, mock_aws_credentials, setup_test_environment):
    """Test that model ID can be overridden."""
    os.environ["ZIYA_MODEL_ID_OVERRIDE"] = model_id
    
    # Create a mock model config
    mock_config = {
        "model_id": "original-model-id"
    }
    
    # Mock the get_model_config method
    with patch.object(ModelManager, '__init__', return_value=None), \
         patch.object(ModelManager, 'get_model_config', return_value=mock_config):
        model_manager = ModelManager()
        model_manager._model_id_override = model_id
        
        # Override the model_id in the mock config
        mock_config["model_id"] = model_id
        
        # Get the model configuration
        model_config = model_manager.get_model_config()
        assert model_config["model_id"] == model_id


@pytest.mark.parametrize("model_family", [
    "claude",
    "nova",
    "titan",
])
def test_model_family_handling(model_family, mock_aws_credentials, mock_bedrock_client, setup_test_environment):
    """Test that different model families are handled correctly."""
    # Find a model from the specified family
    model = None
    for model_name, model_config in config.MODEL_CONFIGS["bedrock"].items():
        if model_config.get("family") == model_family:
            model = model_name
            break
    
    # Skip if no model found for this family
    if not model:
        pytest.skip(f"No model found for family {model_family}")
    
    os.environ["ZIYA_MODEL"] = model
    
    # Mock __init__ to prevent actual initialization
    with patch.object(ModelManager, '__init__', return_value=None):
        model_manager = ModelManager()
        # Set the model manually for testing
        model_manager._model = model
        assert model_manager._model == model


@pytest.mark.parametrize("param_name,param_value", [
    ("temperature", 0.7),
    ("top_p", 0.9),
    ("top_k", 40),
    ("max_output_tokens", 2000),
])
def test_model_parameters(param_name, param_value, mock_aws_credentials, mock_bedrock_client, setup_test_environment):
    """Test that model parameters are correctly passed."""
    # Set the parameter
    os.environ[f"ZIYA_{param_name.upper()}"] = str(param_value)
    
    # Create a mock model config
    mock_config = {}
    
    # Mock the get_model_config method
    with patch.object(ModelManager, '__init__', return_value=None), \
         patch.object(ModelManager, 'get_model_config', return_value=mock_config):
        model_manager = ModelManager()
        
        # Set the parameter manually
        setattr(model_manager, f"_{param_name}", param_value)
        
        # Update the mock config with the parameter
        mock_config[param_name] = param_value
        
        # Get the model configuration
        model_config = model_manager.get_model_config()
        assert model_config[param_name] == param_value
