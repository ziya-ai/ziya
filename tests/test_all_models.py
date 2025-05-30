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

import app.config as config
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
    """Initialize an LLM for the given endpoint and model."""
    # Set environment variables
    os.environ["ZIYA_ENDPOINT"] = endpoint
    os.environ["ZIYA_MODEL"] = model
    
    # For Bedrock, always use the ziya profile
    if endpoint == "bedrock":
        os.environ["ZIYA_AWS_PROFILE"] = "ziya"
        os.environ["AWS_PROFILE"] = "ziya"
    
    # Initialize model manager
    model_manager = ModelManager()
    
    # Get model configuration
    model_config = model_manager.get_model_config(endpoint, model)
    
    # Initialize the LLM based on endpoint
    if endpoint == "bedrock":
        from langchain_aws import ChatBedrock
        import boto3
        
        # Create a session with the ziya profile
        session = boto3.Session(profile_name="ziya")
        
        # Create the client using the session
        client = session.client('bedrock-runtime')
        
        # Create the LLM
        llm = ChatBedrock(
            model_id=model_config["model_id"],
            client=client,
            model_kwargs={
                "temperature": float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
                "top_p": float(os.environ.get("ZIYA_TOP_P", 0.9)),
                "top_k": int(os.environ.get("ZIYA_TOP_K", 40)),
                "max_tokens": int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
            }
        )
        return llm, model_config
    
    elif endpoint == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        # Check if Google credentials are available
        if not check_google_credentials():
            pytest.fail("Google API key or credentials not available. Please set GOOGLE_API_KEY environment variable or configure application default credentials.")
        
        # Create the LLM
        llm = ChatGoogleGenerativeAI(
            model=model_config["model_id"],
            temperature=float(os.environ.get("ZIYA_TEMPERATURE", 0.3)),
            top_p=float(os.environ.get("ZIYA_TOP_P", 0.9)),
            top_k=int(os.environ.get("ZIYA_TOP_K", 40)),
            max_output_tokens=int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 4096))
        )
        return llm, model_config
    
    else:
        raise ValueError(f"Unsupported endpoint: {endpoint}")


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
    
    # Skip specific models that are known to have issues
    if model == "sonnet3.7":
        pytest.skip("Skipping sonnet3.7 due to throttling issues")
    
    if model == "deepseek-r1":
        pytest.skip("Skipping deepseek-r1 as it doesn't support chat interface")
        
    # Skip gemini-pro as it's been deprecated in favor of gemini-1.5-pro
    if model == "gemini-pro":
        pytest.skip("Skipping gemini-pro as it's been deprecated in favor of gemini-1.5-pro")
    
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
        response = llm.invoke(extended_prompt)
        end_time = time.time()
        
        # Extract the content from the response
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = str(response)
        
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
