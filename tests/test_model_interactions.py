"""Test suite for verifying model interactions work across all available models."""

import pytest
import os
import sys

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.config import MODEL_CONFIGS


class TestModelInteractions:
    """Test model interactions for all configured models."""
    
    def test_all_models_have_required_config(self):
        """Test that all models have required configuration fields."""
        required_fields = ['model_id']
        
        for endpoint, models in MODEL_CONFIGS.items():
            for model_name, config in models.items():
                # Check required fields exist
                for field in required_fields:
                    assert field in config, f"Model {endpoint}/{model_name} missing {field}"
                
                # Check model_id format
                model_id = config['model_id']
                if isinstance(model_id, dict):
                    assert len(model_id) > 0, f"Model {endpoint}/{model_name} has empty model_id dict"
                else:
                    assert isinstance(model_id, str), f"Model {endpoint}/{model_name} model_id must be string or dict"
    
    def test_model_parameter_validation(self):
        """Test parameter validation for all models."""
        from app.config import get_supported_parameters, validate_model_parameters
        
        for endpoint, models in MODEL_CONFIGS.items():
            for model_name in models.keys():
                # Get supported parameters
                params = get_supported_parameters(endpoint, model_name)
                
                # Test with valid parameters
                if params:
                    test_params = {}
                    for param, constraints in params.items():
                        if 'default' in constraints:
                            test_params[param] = constraints['default']
                    
                    if test_params:
                        is_valid, error, filtered = validate_model_parameters(endpoint, model_name, test_params)
                        assert is_valid, f"Valid parameters failed for {endpoint}/{model_name}: {error}"
                
                # Test with invalid parameter
                invalid_params = {'invalid_param': 1.0}
                is_valid, error, filtered = validate_model_parameters(endpoint, model_name, invalid_params)
                assert not is_valid, f"Invalid parameter should fail for {endpoint}/{model_name}"
    
    @pytest.mark.parametrize("endpoint,model_name", [
        (endpoint, model) 
        for endpoint, models in MODEL_CONFIGS.items() 
        for model in models.keys()
    ])
    def test_model_config_completeness(self, endpoint, model_name):
        """Test that each model has complete configuration."""
        config = MODEL_CONFIGS[endpoint][model_name]
        
        # Check model_id exists and is valid
        assert 'model_id' in config
        model_id = config['model_id']
        
        if isinstance(model_id, dict):
            assert len(model_id) > 0, f"Empty model_id dict for {endpoint}/{model_name}"
            for region, id_val in model_id.items():
                assert isinstance(id_val, str) and len(id_val) > 0, f"Invalid model_id for {endpoint}/{model_name} in region {region}"
        else:
            assert isinstance(model_id, str) and len(model_id) > 0, f"Invalid model_id for {endpoint}/{model_name}"
        
        # If family is specified, it should exist in MODEL_FAMILIES
        if 'family' in config:
            from app.config import MODEL_FAMILIES
            family = config['family']
            assert family in MODEL_FAMILIES, f"Unknown family '{family}' for {endpoint}/{model_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
