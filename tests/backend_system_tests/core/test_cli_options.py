"""
Tests for CLI options and argument parsing.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

import app.config as config
from app.main import parse_arguments, validate_model_and_endpoint, setup_environment


def test_parse_arguments():
    """Test argument parsing."""
    # Test with no arguments
    with patch('sys.argv', ['ziya']):
        args = parse_arguments()
        assert args.exclude == []
        assert args.profile is None
        assert args.endpoint == config.DEFAULT_ENDPOINT
        assert args.model is None
        assert args.model_id is None
        assert args.port == config.DEFAULT_PORT
        assert args.temperature is None
        assert args.top_p is None
        assert args.top_k is None
        assert args.max_output_tokens is None
    
    # Test with all arguments
    with patch('sys.argv', [
        'ziya',
        '--exclude', 'node_modules,venv',
        '--profile', 'test-profile',
        '--endpoint', 'bedrock',
        '--model', 'sonnet3.5',
        '--model-id', 'anthropic.claude-3-sonnet-20240229-v1:0',
        '--port', '8080',
        '--temperature', '0.7',
        '--top-p', '0.9',
        '--top-k', '40',
        '--max-output-tokens', '2000',
    ]):
        args = parse_arguments()
        assert args.exclude == ['node_modules', 'venv']
        assert args.profile == 'test-profile'
        assert args.endpoint == 'bedrock'
        assert args.model == 'sonnet3.5'
        assert args.model_id == 'anthropic.claude-3-sonnet-20240229-v1:0'
        assert args.port == 8080
        assert args.temperature == 0.7
        assert args.top_p == 0.9
        assert args.top_k == 40
        assert args.max_output_tokens == 2000


def test_validate_model_and_endpoint():
    """Test model and endpoint validation."""
    # Test valid model and endpoint
    is_valid, error_message = validate_model_and_endpoint("bedrock", "sonnet3.5")
    assert is_valid
    assert error_message is None
    
    # Test invalid endpoint
    is_valid, error_message = validate_model_and_endpoint("invalid-endpoint", "sonnet3.5")
    assert not is_valid
    assert "Invalid endpoint" in error_message
    
    # Test invalid model
    is_valid, error_message = validate_model_and_endpoint("bedrock", "invalid-model")
    assert not is_valid
    assert "Invalid model" in error_message
    
    # Test None model (should use default)
    is_valid, error_message = validate_model_and_endpoint("bedrock", None)
    assert is_valid
    assert error_message is None


@pytest.mark.parametrize("args_dict", [
    {},  # Default values
    {"exclude": ["node_modules", "venv"]},
    {"profile": "test-profile"},
    {"endpoint": "bedrock", "model": "sonnet3.5"},
    {"model_id": "anthropic.claude-3-sonnet-20240229-v1:0"},
    {"temperature": 0.7, "top_p": 0.9, "top_k": 40, "max_output_tokens": 2000},
])
def test_setup_environment(args_dict, monkeypatch):
    """Test environment setup with different arguments."""
    # Create a mock args object
    args = MagicMock()
    
    # Set default values
    args.exclude = []
    args.profile = None
    args.endpoint = "bedrock"
    args.model = "sonnet3.5"
    args.model_id = None
    args.max_depth = 5
    args.ast = False
    args.temperature = None
    args.top_p = None
    args.top_k = None
    args.max_output_tokens = None
    
    # Override with test values
    for key, value in args_dict.items():
        setattr(args, key, value)
    
    # Save original environment variables
    original_env = os.environ.copy()
    
    try:
        # Mock validate_model_and_endpoint to always return valid
        with patch('app.main.validate_model_and_endpoint', return_value=(True, None)):
            # Call setup_environment
            setup_environment(args)
            
            # Check that environment variables were set correctly
            assert os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] == ','.join(args.exclude)
            
            if args.profile:
                assert os.environ["ZIYA_AWS_PROFILE"] == args.profile
            
            assert os.environ["ZIYA_ENDPOINT"] == args.endpoint
            
            if args.model:
                assert os.environ["ZIYA_MODEL"] == args.model
            
            assert os.environ["ZIYA_MAX_DEPTH"] == str(args.max_depth)
            
            if args.model_id:
                assert os.environ["ZIYA_MODEL_ID_OVERRIDE"] == args.model_id
            
            if args.temperature is not None:
                assert os.environ["ZIYA_TEMPERATURE"] == str(args.temperature)
            
            if args.top_p is not None:
                assert os.environ["ZIYA_TOP_P"] == str(args.top_p)
            
            if args.top_k is not None:
                assert os.environ["ZIYA_TOP_K"] == str(args.top_k)
            
            if args.max_output_tokens is not None:
                assert os.environ["ZIYA_MAX_OUTPUT_TOKENS"] == str(args.max_output_tokens)
    finally:
        # Restore original environment variables
        os.environ.clear()
        os.environ.update(original_env)
