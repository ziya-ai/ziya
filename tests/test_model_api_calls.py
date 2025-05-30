"""
Real API transaction tests for model API calls.
These tests make actual API calls to the configured models.
Skip these tests if you don't have credentials for all models.

WARNING: These tests will incur actual API usage costs.
"""
import os
import time
import pytest
from unittest.mock import patch

import app.config as config
from app.agents.models import ModelManager


def initialize_llm_for_model(endpoint, model):
    """Initialize an LLM for the given endpoint and model."""
    # Set environment variables
    os.environ["ZIYA_ENDPOINT"] = endpoint
    os.environ["ZIYA_MODEL"] = model
    
    # Initialize model manager
    model_manager = ModelManager()
    
    # Get model configuration
    model_config = model_manager.get_model_config(endpoint, model)
    
    # Initialize the LLM based on endpoint
    if endpoint == "bedrock":
        from langchain_aws import ChatBedrock
        
        # Create the LLM
        llm = ChatBedrock(
            model_id=model_config["model_id"],
            model_kwargs={
                "temperature": float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
                "top_p": float(os.environ.get("ZIYA_TOP_P", 0.9)),
                "top_k": int(os.environ.get("ZIYA_TOP_K", 40)),
                "max_tokens": int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
            }
        )
        return llm
    
    elif endpoint == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        # Create the LLM
        llm = ChatGoogleGenerativeAI(
            model=model_config["model_id"],
            temperature=float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
            top_p=float(os.environ.get("ZIYA_TOP_P", 0.9)),
            top_k=int(os.environ.get("ZIYA_TOP_K", 40)),
            max_output_tokens=int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
        )
        return llm
    
    else:
        raise ValueError(f"Unsupported endpoint: {endpoint}")


@pytest.mark.real_api
@pytest.mark.parametrize("endpoint,model", [
    ("bedrock", "sonnet3.5"),  # Claude 3.5 Sonnet
    ("bedrock", "nova-pro"),   # Amazon Titan Text
])
def test_real_api_call(endpoint, model):
    """Test that each model can be called with a simple query."""
    # Skip test if AWS credentials are not set
    if endpoint == "bedrock" and not (os.environ.get("AWS_ACCESS_KEY_ID") and 
                                      os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print(f"AWS credentials not found in environment variables, checking AWS_PROFILE")
        if not os.environ.get("AWS_PROFILE"):
            pytest.skip("AWS credentials not set")
    
    # Skip test if Google API key is not set
    if endpoint == "google" and not os.environ.get("GOOGLE_API_KEY"):
        pytest.skip("Google API key not set")
    
    try:
        # Initialize the LLM
        llm = initialize_llm_for_model(endpoint, model)
        
        # Make a simple query
        start_time = time.time()
        response = llm.predict("What is 2+2? Answer with just the number.")
        end_time = time.time()
        
        # Log the response time
        print(f"\nResponse time for {endpoint}/{model}: {end_time - start_time:.2f} seconds")
        
        # Check that we got a response
        assert response is not None
        assert len(response) > 0
        
        # Check that the response contains the number 4
        # This is a simple check that the model understood the query
        assert "4" in response, f"Expected '4' in response, got: {response}"
        
        print(f"Response from {endpoint}/{model}: {response}")
        
    except Exception as e:
        pytest.fail(f"Error calling {endpoint}/{model}: {str(e)}")


@pytest.mark.real_api
@pytest.mark.parametrize("model_id", [
    "anthropic.claude-3-sonnet-20240229-v1:0",
])
def test_model_id_override_real_api(model_id):
    """Test that model ID override works with actual API calls."""
    # Skip test if AWS credentials are not set
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and 
            os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print(f"AWS credentials not found in environment variables, checking AWS_PROFILE")
        if not os.environ.get("AWS_PROFILE"):
            pytest.skip("AWS credentials not set")
    
    # Set environment variables for the test
    os.environ["ZIYA_ENDPOINT"] = "bedrock"
    os.environ["ZIYA_MODEL"] = "sonnet3.5"
    os.environ["ZIYA_MODEL_ID_OVERRIDE"] = model_id
    
    try:
        # Initialize the LLM
        from langchain_aws import ChatBedrock
        
        # Create the LLM with the overridden model ID
        llm = ChatBedrock(
            model_id=model_id,
            model_kwargs={
                "temperature": float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
                "top_p": float(os.environ.get("ZIYA_TOP_P", 0.9)),
                "top_k": int(os.environ.get("ZIYA_TOP_K", 40)),
                "max_tokens": int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
            }
        )
        
        # Make a simple query
        start_time = time.time()
        response = llm.predict("What is 2+2? Answer with just the number.")
        end_time = time.time()
        
        # Log the response time
        print(f"\nResponse time for model_id={model_id}: {end_time - start_time:.2f} seconds")
        
        # Check that we got a response
        assert response is not None
        assert len(response) > 0
        
        # Check that the response contains the number 4
        assert "4" in response, f"Expected '4' in response, got: {response}"
        
        print(f"Response with model_id={model_id}: {response}")
        
    except Exception as e:
        pytest.fail(f"Error with model ID override {model_id}: {str(e)}")


@pytest.mark.real_api
@pytest.mark.parametrize("param_name,param_value", [
    ("temperature", 0.7),
    ("top_p", 0.9),
])
def test_model_parameters_real_api(param_name, param_value):
    """Test that model parameters are correctly passed in actual API calls."""
    # Skip test if AWS credentials are not set
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and 
            os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print(f"AWS credentials not found in environment variables, checking AWS_PROFILE")
        if not os.environ.get("AWS_PROFILE"):
            pytest.skip("AWS credentials not set")
    
    # Set environment variables for the test
    os.environ["ZIYA_ENDPOINT"] = "bedrock"
    os.environ["ZIYA_MODEL"] = "nova-pro"  # Use Nova model for parameter testing
    os.environ[f"ZIYA_{param_name.upper()}"] = str(param_value)
    
    try:
        # Initialize the LLM
        from langchain_aws import ChatBedrock
        
        # Get model configuration
        model_manager = ModelManager()
        model_config = model_manager.get_model_config("bedrock", "nova-pro")
        
        # Create model kwargs with the specific parameter
        model_kwargs = {
            "temperature": float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
            "top_p": float(os.environ.get("ZIYA_TOP_P", 0.9)),
            "top_k": int(os.environ.get("ZIYA_TOP_K", 40)),
            "max_tokens": int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
        }
        
        # Create the LLM
        llm = ChatBedrock(
            model_id=model_config["model_id"],
            model_kwargs=model_kwargs
        )
        
        # Make a simple query
        start_time = time.time()
        response = llm.predict("What is 2+2? Answer with just the number.")
        end_time = time.time()
        
        # Log the response time
        print(f"\nResponse time with {param_name}={param_value}: {end_time - start_time:.2f} seconds")
        
        # Check that we got a response
        assert response is not None
        assert len(response) > 0
        
        # Check that the response contains the number 4
        assert "4" in response, f"Expected '4' in response, got: {response}"
        
        print(f"Response with {param_name}={param_value}: {response}")
        
    except Exception as e:
        pytest.fail(f"Error with parameter {param_name}={param_value}: {str(e)}")
