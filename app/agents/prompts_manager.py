"""
Prompt management module for Ziya.

This module integrates the prompt extension framework with the existing prompt system.
"""

import os
import hashlib
from typing import Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager
from app.utils.prompt_extensions import PromptExtensionManager
from app.agents.prompts import original_template, conversational_prompt

# Global cache for extended prompts
_prompt_cache = {}

def get_extended_prompt(model_name: Optional[str] = None, 
                       model_family: Optional[str] = None,
                       endpoint: Optional[str] = None,
                       context: Optional[Dict[str, Any]] = None) -> ChatPromptTemplate:
    """
    Get a prompt with extensions applied based on model, family, and endpoint.
    
    Args:
        model_name: Name of the model
        model_family: Family of the model
        endpoint: Endpoint being used
        context: Additional context for extensions
        
    Returns:
        ChatPromptTemplate: The extended prompt template
    """
    if context is None:
        context = {}
    logger.debug(f"Getting extended prompt for model: {model_name}")
    
    # Create cache key from parameters
    cache_data = {
        'model_name': model_name,
        'model_family': model_family, 
        'endpoint': endpoint,
        'context': context,
        'template_length': len(original_template)
    }
    cache_key = hashlib.md5(str(sorted(cache_data.items())).encode()).hexdigest()[:8]
    
    # Check cache
    if cache_key in _prompt_cache:
        logger.info(f"Using cached extended prompt for {cache_key}")
        return _prompt_cache[cache_key]
    
    logger.info(f"Creating new extended prompt for {cache_key}")
    
    # Get the original template
    template = original_template
    
    logger.debug(f"Original template length: {len(template)}")
    # Apply extensions
    extended_template = PromptExtensionManager.apply_extensions(
        prompt=template,
        model_name=model_name,
        model_family=model_family,
        endpoint=endpoint,
        context=context
    )
    
    logger.debug(f"Extended template length: {len(extended_template)}")
    logger.debug(f"PROMPT_MANAGER: Final extended template length: {len(extended_template)}")
    logger.debug(f"PROMPT_MANAGER: Original template length: {len(original_template)}")
    logger.debug(f"PROMPT_MANAGER: Extended template length: {len(extended_template)}")
    logger.debug(f"PROMPT_MANAGER: Template was modified: {len(extended_template) != len(original_template)}")
    
    # Create a new prompt template with the extended template
    # Build messages list dynamically
    messages = [
        ("system", extended_template),
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("user", "{question}"),
    ]
    
    # Only add AST context system message if AST is enabled
    if os.environ.get("ZIYA_ENABLE_AST", "false").lower() in ("true", "1", "yes"):
        messages.append(("system", "{ast_context}"))
    
    messages.append(MessagesPlaceholder(variable_name="agent_scratchpad", optional=True))
    
    extended_prompt = ChatPromptTemplate.from_messages(messages)
    
    # Cache the result
    _prompt_cache[cache_key] = extended_prompt
    logger.info(f"Cached extended prompt for {cache_key}")
    
    return extended_prompt

def invalidate_prompt_cache():
    """Invalidate the prompt extension cache to force fresh prompt generation."""
    global _prompt_cache
    _prompt_cache = {}
    logger.info("Prompt extension cache invalidated")

def get_model_info_from_config() -> Dict[str, str]:
    """
    Get model information from environment variables or config.
    
    Returns:
        Dict[str, str]: Dictionary with model_name, model_family, and endpoint
    """
    from app.config.models_config import DEFAULT_ENDPOINT, DEFAULT_MODELS, MODEL_CONFIGS
    
    # Get endpoint and model from environment variables
    endpoint = os.environ.get("ZIYA_ENDPOINT", DEFAULT_ENDPOINT)
    model_name = os.environ.get("ZIYA_MODEL", DEFAULT_MODELS.get(endpoint))
    
    # Get model family from config
    model_family = None
    if endpoint in MODEL_CONFIGS and model_name in MODEL_CONFIGS[endpoint]:
        model_family = MODEL_CONFIGS[endpoint][model_name].get("family")
    
    return {
        "model_name": model_name,
        "model_family": model_family,
        "endpoint": endpoint
    }

def initialize_prompt_system():
    """
    Initialize the prompt system.
    """
    # Initialize extensions
    from app.extensions import init_extensions
    init_extensions()
    
    # Log that the prompt system is initialized
    logger.info("Prompt system initialized with extensions")
