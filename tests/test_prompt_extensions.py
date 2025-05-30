"""
Tests for the prompt extension framework.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from app.utils.prompt_extensions import PromptExtensionManager, prompt_extension
from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
from app.extensions import init_extensions


def test_prompt_extension_registration():
    """Test that prompt extensions can be registered."""
    # Create a test extension
    @prompt_extension(
        name="test_extension",
        extension_type="global",
        config={"enabled": True}
    )
    def test_extension(prompt, context):
        return prompt + " TEST_EXTENSION"
    
    # Apply the extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(original_prompt)
    
    # Check that the extension was applied
    assert "TEST_EXTENSION" in modified_prompt
    assert modified_prompt == "This is a test prompt. TEST_EXTENSION"


def test_model_specific_extension():
    """Test that model-specific extensions are applied correctly."""
    # Create a model-specific extension
    @prompt_extension(
        name="test_model_extension",
        extension_type="model",
        target="test-model",
        config={"enabled": True}
    )
    def test_model_extension(prompt, context):
        return prompt + " MODEL_EXTENSION"
    
    # Apply the extension with the matching model
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        model_name="test-model"
    )
    
    # Check that the extension was applied
    assert "MODEL_EXTENSION" in modified_prompt
    
    # Apply with a different model
    different_model_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        model_name="different-model"
    )
    
    # Check that the extension was not applied
    assert "MODEL_EXTENSION" not in different_model_prompt


def test_family_specific_extension():
    """Test that family-specific extensions are applied correctly."""
    # Create a family-specific extension
    @prompt_extension(
        name="test_family_extension",
        extension_type="family",
        target="test-family",
        config={"enabled": True}
    )
    def test_family_extension(prompt, context):
        return prompt + " FAMILY_EXTENSION"
    
    # Apply the extension with the matching family
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        model_family="test-family"
    )
    
    # Check that the extension was applied
    assert "FAMILY_EXTENSION" in modified_prompt
    
    # Apply with a different family
    different_family_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        model_family="different-family"
    )
    
    # Check that the extension was not applied
    assert "FAMILY_EXTENSION" not in different_family_prompt


def test_endpoint_specific_extension():
    """Test that endpoint-specific extensions are applied correctly."""
    # Create an endpoint-specific extension
    @prompt_extension(
        name="test_endpoint_extension",
        extension_type="endpoint",
        target="test-endpoint",
        config={"enabled": True}
    )
    def test_endpoint_extension(prompt, context):
        return prompt + " ENDPOINT_EXTENSION"
    
    # Apply the extension with the matching endpoint
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        endpoint="test-endpoint"
    )
    
    # Check that the extension was applied
    assert "ENDPOINT_EXTENSION" in modified_prompt
    
    # Apply with a different endpoint
    different_endpoint_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        endpoint="different-endpoint"
    )
    
    # Check that the extension was not applied
    assert "ENDPOINT_EXTENSION" not in different_endpoint_prompt


def test_multiple_extensions():
    """Test that multiple extensions are applied in the correct order."""
    # Create multiple extensions
    @prompt_extension(
        name="test_global_extension",
        extension_type="global",
        config={"enabled": True, "priority": 1}
    )
    def test_global_extension(prompt, context):
        return prompt + " GLOBAL_EXTENSION"
    
    @prompt_extension(
        name="test_endpoint_extension",
        extension_type="endpoint",
        target="test-endpoint",
        config={"enabled": True, "priority": 2}
    )
    def test_endpoint_extension(prompt, context):
        return prompt + " ENDPOINT_EXTENSION"
    
    @prompt_extension(
        name="test_family_extension",
        extension_type="family",
        target="test-family",
        config={"enabled": True, "priority": 3}
    )
    def test_family_extension(prompt, context):
        return prompt + " FAMILY_EXTENSION"
    
    @prompt_extension(
        name="test_model_extension",
        extension_type="model",
        target="test-model",
        config={"enabled": True, "priority": 4}
    )
    def test_model_extension(prompt, context):
        return prompt + " MODEL_EXTENSION"
    
    # Apply all extensions
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt, 
        model_name="test-model",
        model_family="test-family",
        endpoint="test-endpoint"
    )
    
    # Check that all extensions were applied in the correct order
    assert "GLOBAL_EXTENSION" in modified_prompt
    assert "ENDPOINT_EXTENSION" in modified_prompt
    assert "FAMILY_EXTENSION" in modified_prompt
    assert "MODEL_EXTENSION" in modified_prompt
    
    # Check the order (should be global, endpoint, family, model)
    global_pos = modified_prompt.find("GLOBAL_EXTENSION")
    endpoint_pos = modified_prompt.find("ENDPOINT_EXTENSION")
    family_pos = modified_prompt.find("FAMILY_EXTENSION")
    model_pos = modified_prompt.find("MODEL_EXTENSION")
    
    assert global_pos < endpoint_pos < family_pos < model_pos


def test_disabled_extension():
    """Test that disabled extensions are not applied."""
    # Create a disabled extension
    @prompt_extension(
        name="test_disabled_extension",
        extension_type="global",
        config={"enabled": False}
    )
    def test_disabled_extension(prompt, context):
        return prompt + " DISABLED_EXTENSION"
    
    # Apply the extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(original_prompt)
    
    # Check that the extension was not applied
    assert "DISABLED_EXTENSION" not in modified_prompt


def test_extension_with_context():
    """Test that extensions can use context."""
    # Create an extension that uses context
    @prompt_extension(
        name="test_context_extension",
        extension_type="global",
        config={"enabled": True}
    )
    def test_context_extension(prompt, context):
        return prompt + f" CONTEXT: {context.get('test_value', 'default')}"
    
    # Apply the extension with context
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt,
        context={"test_value": "custom_value"}
    )
    
    # Check that the extension used the context
    assert "CONTEXT: custom_value" in modified_prompt


def test_extension_error_handling():
    """Test that extension errors are handled gracefully."""
    # Reset the extension manager to start with a clean slate
    PromptExtensionManager._extensions = {
        "model": {},
        "family": {},
        "endpoint": {},
        "global": {}
    }
    PromptExtensionManager._config = {
        "model": {},
        "family": {},
        "endpoint": {},
        "global": {}
    }
    
    # Create an extension that raises an error
    @prompt_extension(
        name="test_error_extension",
        extension_type="global",
        config={"enabled": True}
    )
    def test_error_extension(prompt, context):
        raise ValueError("Test error")
    
    # Apply the extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(original_prompt)
    
    # Check that the original prompt is returned (unchanged)
    assert modified_prompt == original_prompt


@patch('app.agents.prompts_manager.get_model_info_from_config')
def test_get_extended_prompt(mock_get_model_info):
    """Test that get_extended_prompt applies the correct extensions."""
    # Mock the model info
    mock_get_model_info.return_value = {
        "model_name": "test-model",
        "model_family": "test-family",
        "endpoint": "test-endpoint"
    }
    
    # Create extensions for each level
    @prompt_extension(
        name="test_model_extension",
        extension_type="model",
        target="test-model",
        config={"enabled": True}
    )
    def test_model_extension(prompt, context):
        return prompt + " MODEL_EXTENSION"
    
    @prompt_extension(
        name="test_family_extension",
        extension_type="family",
        target="test-family",
        config={"enabled": True}
    )
    def test_family_extension(prompt, context):
        return prompt + " FAMILY_EXTENSION"
    
    @prompt_extension(
        name="test_endpoint_extension",
        extension_type="endpoint",
        target="test-endpoint",
        config={"enabled": True}
    )
    def test_endpoint_extension(prompt, context):
        return prompt + " ENDPOINT_EXTENSION"
    
    # Get the extended prompt
    with patch('app.agents.prompts_manager.original_template', "This is a test prompt."):
        extended_prompt = get_extended_prompt(
            model_name="test-model",
            model_family="test-family",
            endpoint="test-endpoint"
        )
    
    # Check that the prompt template was created with the extended template
    assert isinstance(extended_prompt, object)  # Should be a ChatPromptTemplate


def test_nova_lite_extension():
    """Test that the Nova-Lite extension adds the expected instructions."""
    # Initialize extensions
    init_extensions()
    
    # Apply the Nova-Lite extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt,
        model_name="nova-lite",
        model_family="nova"
    )
    
    # Check that the Nova-Lite specific instructions were added
    assert "NOVA-LITE SPECIFIC INSTRUCTIONS" in modified_prompt
    assert "include full filepaths in your diffs" in modified_prompt.lower()


def test_nova_pro_extension():
    """Test that the Nova-Pro extension adds the expected instructions."""
    # Initialize extensions
    init_extensions()
    
    # Apply the Nova-Pro extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt,
        model_name="nova-pro",
        model_family="nova"
    )
    
    # Check that the Nova-Pro specific instructions were added
    assert "NOVA-PRO THINKING MODE INSTRUCTIONS" in modified_prompt
    assert "<thinking>" in modified_prompt.lower()


def test_claude_family_extension():
    """Test that the Claude family extension adds the expected instructions."""
    # Initialize extensions
    init_extensions()
    
    # Apply the Claude family extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt,
        model_family="claude"
    )
    
    # Check that the Claude family instructions were added
    assert "CLAUDE FAMILY INSTRUCTIONS" in modified_prompt


def test_gemini_family_extension():
    """Test that the Gemini family extension adds the expected instructions."""
    # Initialize extensions
    init_extensions()
    
    # Apply the Gemini family extension
    original_prompt = "This is a test prompt."
    modified_prompt = PromptExtensionManager.apply_extensions(
        original_prompt,
        model_family="gemini"
    )
    
    # Check that the Gemini family instructions were added
    assert "GEMINI FAMILY INSTRUCTIONS" in modified_prompt
