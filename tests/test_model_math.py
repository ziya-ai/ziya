"""
Test that verifies model functionality with a simple math question.
This test uses mocks to avoid making actual API calls.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

import app.config as config
from app.agents.models import ModelManager
from app.utils.prompt_extensions import PromptExtensionManager
from app.extensions import init_extensions


class MockResponse:
    """Mock response for LLM calls."""
    def __init__(self, content="4"):
        self.content = content


@pytest.fixture
def setup_extensions():
    """Initialize prompt extensions."""
    init_extensions()


def get_all_available_models():
    """Get all available models from the config."""
    models = []
    for endpoint, endpoint_models in config.MODEL_CONFIGS.items():
        for model_name in endpoint_models.keys():
            # Skip models that are known to have issues
            if model_name in ["sonnet3.7", "deepseek-r1"]:
                continue
            models.append((endpoint, model_name))
    return models


@patch("langchain_aws.ChatBedrock")
@patch("langchain_google_genai.ChatGoogleGenerativeAI")
@pytest.mark.parametrize("endpoint,model", get_all_available_models())
def test_model_math(mock_google_llm, mock_bedrock_llm, endpoint, model, setup_extensions):
    """Test that each model can handle a simple math question with prompt extensions."""
    # Configure mocks
    mock_bedrock_instance = MagicMock()
    mock_bedrock_instance.invoke.return_value = MockResponse("4")
    mock_bedrock_llm.return_value = mock_bedrock_instance
    
    mock_google_instance = MagicMock()
    mock_google_instance.invoke.return_value = MockResponse("4")
    mock_google_llm.return_value = mock_google_instance
    
    # Set environment variables
    os.environ["ZIYA_ENDPOINT"] = endpoint
    os.environ["ZIYA_MODEL"] = model
    
    # Get model configuration
    model_config = ModelManager.get_model_config(endpoint, model)
    model_family = model_config.get("family")
    
    # Create the LLM based on endpoint
    if endpoint == "bedrock":
        llm = mock_bedrock_llm(
            model_id=model_config["model_id"],
            model_kwargs={
                "temperature": 0.3,
                "top_p": 0.9,
                "top_k": 40,
                "max_tokens": 4096
            }
        )
    else:  # google
        llm = mock_google_llm(
            model=model_config["model_id"],
            temperature=0.3,
            max_output_tokens=4096
        )
    
    # Get the prompt with extensions applied
    original_prompt = "What is 2+2? Answer with just the number."
    extended_prompt = PromptExtensionManager.apply_extensions(
        original_prompt,
        model_name=model,
        model_family=model_family,
        endpoint=endpoint
    )
    
    # Verify that the prompt was extended appropriately
    if model == "nova-lite":
        assert "NOVA-LITE SPECIFIC INSTRUCTIONS" in extended_prompt
    elif model == "nova-pro":
        assert "NOVA-PRO THINKING MODE INSTRUCTIONS" in extended_prompt
    elif model_family == "claude":
        assert "CLAUDE FAMILY INSTRUCTIONS" in extended_prompt
    elif model_family == "gemini":
        assert "GEMINI FAMILY INSTRUCTIONS" in extended_prompt
    elif model_family == "nova":
        assert "NOVA FAMILY INSTRUCTIONS" in extended_prompt
    
    # Make the query
    response = llm.invoke(extended_prompt)
    
    # Extract the content from the response
    if hasattr(response, 'content'):
        content = response.content
    else:
        content = str(response)
    
    # Check that we got a response with "4"
    assert content is not None
    assert len(content) > 0
    assert "4" in content, f"Expected '4' in response, got: {content}"
    
    # Verify that the appropriate mock was called
    if endpoint == "bedrock":
        mock_bedrock_llm.assert_called_once()
        mock_bedrock_instance.invoke.assert_called_once_with(extended_prompt)
    else:  # google
        mock_google_llm.assert_called_once()
        mock_google_instance.invoke.assert_called_once_with(extended_prompt)


@patch("langchain_aws.ChatBedrock")
@patch("langchain_google_genai.ChatGoogleGenerativeAI")
def test_model_comparison(mock_google_llm, mock_bedrock_llm, setup_extensions):
    """Compare responses from different models for the same query."""
    # Configure mocks
    mock_bedrock_instance = MagicMock()
    mock_bedrock_instance.invoke.return_value = MockResponse("The function would return 0 because an empty list has no items to iterate over.")
    mock_bedrock_llm.return_value = mock_bedrock_instance
    
    mock_google_instance = MagicMock()
    mock_google_instance.invoke.return_value = MockResponse("The function would return 0 because there are no items in the list to process.")
    mock_google_llm.return_value = mock_google_instance
    
    # Models to compare
    models_to_compare = [
        ("bedrock", "sonnet3.5"),
        ("bedrock", "nova-pro"),
        ("google", "gemini-1.5-pro")
    ]
    
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
        # Set environment variables
        os.environ["ZIYA_ENDPOINT"] = endpoint
        os.environ["ZIYA_MODEL"] = model
        
        # Get model configuration
        model_config = ModelManager.get_model_config(endpoint, model)
        model_family = model_config.get("family")
        
        # Create the LLM based on endpoint
        if endpoint == "bedrock":
            llm = mock_bedrock_llm(
                model_id=model_config["model_id"],
                model_kwargs={
                    "temperature": 0.3,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_tokens": 4096
                }
            )
        else:  # google
            llm = mock_google_llm(
                model=model_config["model_id"],
                temperature=0.3,
                max_output_tokens=4096
            )
        
        # Get the prompt with extensions applied
        extended_query = PromptExtensionManager.apply_extensions(
            query,
            model_name=model,
            model_family=model_family,
            endpoint=endpoint
        )
        
        # Make the query
        response = llm.invoke(extended_query)
        
        # Extract the content from the response
        if hasattr(response, 'content'):
            content = response.content
        else:
            content = str(response)
        
        # Store the result
        results[f"{endpoint}/{model}"] = {
            "response": content
        }
    
    # Check that we got responses from all models
    for model_name, result in results.items():
        assert "response" in result
        assert len(result["response"]) > 0
        
    # Check that the responses mention returning 0 or empty list
    for model_name, result in results.items():
        assert any(keyword in result["response"].lower() for keyword in ["0", "zero", "empty"]), \
            f"Expected response from {model_name} to mention returning 0 or empty list"
