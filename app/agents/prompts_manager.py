"""
Prompt management module for Ziya.

This module integrates the prompt extension framework with the existing prompt system.
"""

import os
from typing import Dict, Any, Optional
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager
from app.utils.prompt_extensions import PromptExtensionManager
from app.agents.prompts import original_template, conversational_prompt

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
    logger.error(f"🔍 EXECUTION_TRACE: get_extended_prompt() called for model: {model_name}")
    
    # Get the original template
    template = original_template
    
    logger.error(f"🔍 EXECUTION_TRACE: Original template length: {len(template)}")
    # Apply extensions
    extended_template = PromptExtensionManager.apply_extensions(
        prompt=template,
        model_name=model_name,
        model_family=model_family,
        endpoint=endpoint,
        context=context
    )
    
    logger.error(f"🔍 EXECUTION_TRACE: Extended template length: {len(extended_template)}")
    logger.info(f"PROMPT_MANAGER: Final extended template length: {len(extended_template)}")
    logger.info(f"PROMPT_MANAGER: Original template length: {len(template)}")
    logger.info(f"PROMPT_MANAGER: Extended template length: {len(extended_template)}")
    logger.info(f"PROMPT_MANAGER: Template was modified: {len(extended_template) != len(template)}")
    
    # Create a new prompt template with the extended template
    extended_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", extended_template),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("user", "{question}"),
            # Add AST context if available
            ("system", "{ast_context}"),
            MessagesPlaceholder(variable_name="agent_scratchpad", optional=True),
        ]
    )
    
    return extended_prompt

def get_model_info_from_config() -> Dict[str, str]:
    """
    Get model information from environment variables or config.
    
    Returns:
        Dict[str, str]: Dictionary with model_name, model_family, and endpoint
    """
    from app.config import DEFAULT_ENDPOINT, DEFAULT_MODELS, MODEL_CONFIGS
    
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
