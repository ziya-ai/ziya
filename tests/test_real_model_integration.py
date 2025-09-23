"""Integration tests for real model interactions using ziya AWS profile."""

import pytest
import os
import sys
from unittest.mock import patch

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.config import MODEL_CONFIGS


class TestRealModelIntegration:
    """Test real model interactions using ziya AWS profile."""
    
    @pytest.fixture(autouse=True)
    def setup_ziya_profile(self):
        """Set up ziya AWS profile for testing."""
        with patch.dict(os.environ, {
            'AWS_PROFILE': 'ziya',
            'ZIYA_PROFILE': 'ziya',
            'GOOGLE_API_KEY': os.environ.get('GOOGLE_API_KEY', '')
        }):
            yield
    
    @pytest.mark.integration
    @pytest.mark.parametrize("endpoint,model_name", [
        ("bedrock", "sonnet4.0"),
        ("bedrock", "nova-lite"), 
        ("google", "gemini-flash")
    ])
    def test_real_model_chat(self, endpoint, model_name):
        """Test real chat with a subset of models."""
        from app.agents.models import ModelManager
        
        # Set environment for specific model
        with patch.dict(os.environ, {
            'ZIYA_ENDPOINT': endpoint,
            'ZIYA_MODEL': model_name
        }):
            try:
                llm = ModelManager.initialize_model(force_reinit=True)
                
                if llm is None:
                    pytest.skip(f"Could not initialize {endpoint}/{model_name} - credentials may be expired")
                
                # Simple test message
                response = llm.invoke("Say 'test successful'")
                
                assert response is not None
                assert len(str(response)) > 0
                
            except Exception as e:
                if "expired" in str(e).lower() or "credential" in str(e).lower():
                    pytest.skip(f"Skipping {endpoint}/{model_name} - credentials issue: {e}")
                else:
                    raise
    
    @pytest.mark.integration
    def test_model_manager_initialization(self):
        """Test ModelManager can initialize with ziya profile."""
        from app.agents.models import ModelManager
        
        # Test default model works
        llm = ModelManager.initialize_model()
        assert llm is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])
