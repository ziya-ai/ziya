"""
Comprehensive tests for all available models.
These tests make actual API calls to verify that all configured models are working.

WARNING: These tests will incur actual API usage costs.
"""
import os
import time
import pytest
import json
from unittest.mock import patch
from dotenv import load_dotenv, find_dotenv

import app.config.models_config as config
from app.agents.models import ModelManager
from app.utils.prompt_extensions import PromptExtensionManager
from app.extensions import init_extensions


def check_google_credentials():
    """Check if Google credentials are available."""
    # Try to get API key from environment variables
    api_key = os.environ.get("GOOGLE_API_KEY")
    
    # If no API key in environment, try loading from .env file
    if not api_key:
        dotenv_path = find_dotenv()
        if dotenv_path:
            load_dotenv(dotenv_path)
            api_key = os.environ.get("GOOGLE_API_KEY")
            
    # If still no API key, try application default credentials
    if not api_key:
        try:
            import google.auth
            credentials, project = google.auth.default()
            print(f"Using Google application default credentials for project: {project}")
            return True
        except Exception as e:
            print(f"Google credentials not available: {str(e)}")
            return False
    
    return True


def initialize_llm_for_model(endpoint, model):
    """Initialize an LLM for the given endpoint and model using the real ModelManager."""
    # Clean env vars that leak between model inits
    for var in ["ZIYA_MAX_OUTPUT_TOKENS", "ZIYA_MAX_TOKENS", "AWS_REGION"]:
        os.environ.pop(var, None)
    
    os.environ["ZIYA_ENDPOINT"] = endpoint
    os.environ["ZIYA_MODEL"] = model
    
    if endpoint == "bedrock":
        os.environ["ZIYA_AWS_PROFILE"] = "ziya"
        os.environ["AWS_PROFILE"] = "ziya"
    
    # Use the real ModelManager which handles all parameter filtering, region
    # selection, wrapper class routing, and model_id resolution correctly.
    ModelManager._reset_state()
    llm = ModelManager.initialize_model(force_reinit=True)
    model_config = ModelManager.get_model_config(endpoint, model)
    return llm, model_config


def get_all_available_models():
    """Get all available models from the config."""
    models = []
    for endpoint, endpoint_models in config.MODEL_CONFIGS.items():
        for model_name in endpoint_models.keys():
            models.append((endpoint, model_name))
    return models


@pytest.fixture(scope="module")
def setup_extensions():
    """Initialize prompt extensions."""
    init_extensions()
    return True


@pytest.mark.real_api
@pytest.mark.parametrize("endpoint,model", get_all_available_models())
def test_all_models(endpoint, model, setup_extensions):
    """Test that each model can be called with a simple query."""
    # For Bedrock models, always use the ziya profile
    if endpoint == "bedrock":
        os.environ["ZIYA_AWS_PROFILE"] = "ziya"
        os.environ["AWS_PROFILE"] = "ziya"
    
    # For Google models, check credentials before proceeding
    if endpoint == "google" and not check_google_credentials():
        pytest.fail("Google API key or credentials not available. Please set GOOGLE_API_KEY environment variable or configure application default credentials.")
    
    try:
        # Initialize the LLM
        llm, model_config = initialize_llm_for_model(endpoint, model)
        
        # Print model information
        print(f"\nTesting {endpoint}/{model}")
        print(f"Model ID: {model_config.get('model_id')}")
        print(f"Family: {model_config.get('family', 'N/A')}")
        
        # Get the prompt with extensions applied
        original_prompt = "What is 2+2? Answer with just the number."
        extended_prompt = PromptExtensionManager.apply_extensions(
            original_prompt,
            model_name=model,
            model_family=model_config.get("family"),
            endpoint=endpoint
        )
        
        # Make a simple query
        start_time = time.time()
        
        # Google DirectGoogleModel uses async astream, everything else uses invoke
        if hasattr(llm, 'astream') and not hasattr(llm, 'invoke'):
            import asyncio
            from langchain_core.messages import HumanMessage
            
            async def _call_google():
                content_parts = []
                async for chunk in llm.astream([HumanMessage(content=extended_prompt)]):
                    if isinstance(chunk, dict) and chunk.get("type") == "text":
                        content_parts.append(chunk.get("content", ""))
                    elif isinstance(chunk, dict) and chunk.get("type") == "thinking":
                        pass  # skip thinking chunks
                    elif hasattr(chunk, 'content'):
                        content_parts.append(str(chunk.content))
                return "".join(content_parts)
            
            content = asyncio.run(_call_google())
        else:
            response = llm.invoke(extended_prompt)
            if hasattr(response, 'content'):
                content = response.content
            else:
                content = str(response)
        
        end_time = time.time()
        
        # Log the response time
        print(f"Response time: {end_time - start_time:.2f} seconds")
        
        # Check that we got a response
        assert content is not None
        assert len(content) > 0
        
        # Check that the response contains the number 4
        assert "4" in content, f"Expected '4' in response, got: {content}"
        
        print(f"Response: {content}")
        print(f"✅ {endpoint}/{model} - SUCCESS")
        
    except Exception as e:
        print(f"❌ {endpoint}/{model} - FAILED: {str(e)}")
        pytest.fail(f"Error calling {endpoint}/{model}: {str(e)}")


@pytest.mark.real_api
def test_model_comparison(setup_extensions):
    """Compare responses from different models for the same query."""
    # Set AWS profile to ziya for Bedrock models
    os.environ["ZIYA_AWS_PROFILE"] = "ziya"
    os.environ["AWS_PROFILE"] = "ziya"
    
    # Models to compare - one from each family
    models_to_compare = [
        ("bedrock", "sonnet3.5"),
        ("bedrock", "nova-pro"),
    ]
    
    # Add a Gemini model if Google API key is available
    if check_google_credentials():
        models_to_compare.append(("google", "gemini-1.5-pro"))
    else:
        print("⚠️ WARNING: Google credentials not available. Skipping Gemini model in comparison test.")
    
    # Complex query that requires reasoning
    query = """
    Given the following code snippet:
    ```python
    def calculate_total(items):
        total = 0
        for item in items:
            if item.price > 0:
                total += item.price
        return total
    ```
    
    What would happen if we pass an empty list to this function? Explain briefly.
    """
    
    results = {}
    
    for endpoint, model in models_to_compare:
        try:
            # Initialize the LLM
            llm, model_config = initialize_llm_for_model(endpoint, model)
            
            # Get the prompt with extensions applied
            extended_query = PromptExtensionManager.apply_extensions(
                query,
                model_name=model,
                model_family=model_config.get("family"),
                endpoint=endpoint
            )
            
            # Make the query
            start_time = time.time()
            response = llm.invoke(extended_query)
            end_time = time.time()
            
            # Extract the content from the response
            if hasattr(response, 'content'):
                content = response.content
            else:
                content = str(response)
            
            # Store the result
            results[f"{endpoint}/{model}"] = {
                "response": content,
                "time": end_time - start_time
            }
            
            print(f"✅ {endpoint}/{model} - SUCCESS")
            
        except Exception as e:
            print(f"❌ {endpoint}/{model} - FAILED: {str(e)}")
            results[f"{endpoint}/{model}"] = {
                "error": str(e)
            }
    
    # Print comparison
    print("\n=== MODEL COMPARISON ===")
    for model_name, result in results.items():
        print(f"\n{model_name}:")
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            print(f"Response time: {result['time']:.2f} seconds")
            print(f"Response: {result['response']}")
    
    # Check that we got responses from all models
    for model_name, result in results.items():
        assert "error" not in result, f"Error with {model_name}: {result.get('error')}"
        
        # Check that the response mentions returning 0 or empty list
        assert any(keyword in result["response"].lower() for keyword in ["0", "zero", "empty"]), \
            f"Expected response from {model_name} to mention returning 0 or empty list"
