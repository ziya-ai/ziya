"""
Post-instruction framework for Ziya.

This module provides a framework for adding hidden post-instructions to user queries
based on model, model family, or endpoint.
"""

from typing import Dict, List, Optional, Callable, Any, Union
from app.utils.logging_utils import logger

# Type definitions
PostInstructionFn = Callable[[str, Dict[str, Any]], str]
PostInstructionConfig = Dict[str, Any]

class PostInstructionManager:
    """
    Manager for post-instructions that can be applied based on model, model family, or endpoint.
    These instructions are added after user queries but hidden from the user.
    """
    
    # Class-level storage for post-instructions
    _post_instructions: Dict[str, Dict[str, PostInstructionFn]] = {
        "model": {},       # Post-instructions for specific models
        "family": {},      # Post-instructions for model families
        "endpoint": {},    # Post-instructions for endpoints
        "global": {}       # Global post-instructions applied to all queries
    }
    
    # Post-instruction configuration
    _config: Dict[str, Dict[str, PostInstructionConfig]] = {
        "model": {},
        "family": {},
        "endpoint": {},
        "global": {}
    }
    
    @classmethod
    def register_post_instruction(cls, 
                                post_instruction_fn: PostInstructionFn, 
                                name: str, 
                                instruction_type: str = "global", 
                                target: Optional[str] = None,
                                config: Optional[Dict[str, Any]] = None) -> None:
        """
        Register a post-instruction function.
        
        Args:
            post_instruction_fn: Function that takes a query and context and returns a modified query
            name: Unique name for the post-instruction
            instruction_type: Type of post-instruction ("model", "family", "endpoint", or "global")
            target: Target identifier (model name, family name, or endpoint name)
            config: Optional configuration for the post-instruction
        """
        if instruction_type not in cls._post_instructions:
            raise ValueError(f"Invalid instruction type: {instruction_type}. Must be one of: {', '.join(cls._post_instructions.keys())}")
        
        # For non-global post-instructions, target is required
        if instruction_type != "global" and not target:
            raise ValueError(f"Target is required for {instruction_type} post-instructions")
        
        # Create a unique key for the post-instruction
        key = target if target else name
        
        # Register the post-instruction
        cls._post_instructions[instruction_type][key] = post_instruction_fn
        
        # Store the configuration
        if config:
            if key not in cls._config[instruction_type]:
                cls._config[instruction_type][key] = {}
            cls._config[instruction_type][key] = config
        
        logger.debug(f"Registered post-instruction '{name}' for {instruction_type}{': ' + target if target else ''}")
    
    @classmethod
    def apply_post_instructions(cls, 
                              query: str, 
                              model_name: Optional[str] = None, 
                              model_family: Optional[str] = None,
                              endpoint: Optional[str] = None,
                              context: Optional[Dict[str, Any]] = None) -> str:
        """
        Apply all relevant post-instructions to a user query.
        
        Args:
            query: The original user query
            model_name: Name of the model
            model_family: Family of the model
            endpoint: Endpoint being used
            context: Additional context for post-instructions
            
        Returns:
            str: The modified query with post-instructions
        """
        if context is None:
            context = {}
        
        # Start with the original query
        modified_query = query
        
        # Apply global post-instructions first
        for name, post_instruction_fn in cls._post_instructions["global"].items():
            instruction_context = {**context, "config": cls._config["global"].get(name, {})}
            try:
                # Check if the post-instruction is enabled
                if not instruction_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled global post-instruction '{name}'")
                    continue
                    
                modified_query = post_instruction_fn(modified_query, instruction_context)
                logger.debug(f"Applied global post-instruction '{name}'")
            except Exception as e:
                logger.error(f"Error applying global post-instruction '{name}': {e}")
        
        # Apply endpoint post-instructions if endpoint is specified
        if endpoint and endpoint in cls._post_instructions["endpoint"]:
            post_instruction_fn = cls._post_instructions["endpoint"][endpoint]
            instruction_context = {**context, "config": cls._config["endpoint"].get(endpoint, {})}
            try:
                # Check if the post-instruction is enabled
                if not instruction_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled endpoint post-instruction for '{endpoint}'")
                else:
                    modified_query = post_instruction_fn(modified_query, instruction_context)
                    logger.debug(f"Applied endpoint post-instruction for '{endpoint}'")
            except Exception as e:
                logger.error(f"Error applying endpoint post-instruction for '{endpoint}': {e}")
        
        # Apply family post-instructions if family is specified
        if model_family and model_family in cls._post_instructions["family"]:
            post_instruction_fn = cls._post_instructions["family"][model_family]
            instruction_context = {**context, "config": cls._config["family"].get(model_family, {})}
            try:
                # Check if the post-instruction is enabled
                if not instruction_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled family post-instruction for '{model_family}'")
                else:
                    modified_query = post_instruction_fn(modified_query, instruction_context)
                    logger.debug(f"Applied family post-instruction for '{model_family}'")
            except Exception as e:
                logger.error(f"Error applying family post-instruction for '{model_family}': {e}")
        
        # Apply model post-instructions if model is specified
        if model_name and model_name in cls._post_instructions["model"]:
            post_instruction_fn = cls._post_instructions["model"][model_name]
            instruction_context = {**context, "config": cls._config["model"].get(model_name, {})}
            try:
                # Check if the post-instruction is enabled
                if not instruction_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled model post-instruction for '{model_name}'")
                else:
                    modified_query = post_instruction_fn(modified_query, instruction_context)
                    logger.debug(f"Applied model post-instruction for '{model_name}'")
            except Exception as e:
                logger.error(f"Error applying model post-instruction for '{model_name}': {e}")
        
        return modified_query
    
    @classmethod
    def get_post_instruction_config(cls, 
                                  instruction_type: str, 
                                  target: str) -> Optional[Dict[str, Any]]:
        """
        Get the configuration for a post-instruction.
        
        Args:
            instruction_type: Type of post-instruction
            target: Target identifier
            
        Returns:
            Optional[Dict[str, Any]]: The post-instruction configuration or None if not found
        """
        if instruction_type not in cls._config:
            return None
        
        return cls._config[instruction_type].get(target)
    
    @classmethod
    def update_post_instruction_config(cls,
                                     instruction_type: str,
                                     target: str,
                                     config: Dict[str, Any]) -> None:
        """
        Update the configuration for a post-instruction.
        
        Args:
            instruction_type: Type of post-instruction
            target: Target identifier
            config: New configuration
        """
        if instruction_type not in cls._config:
            raise ValueError(f"Invalid instruction type: {instruction_type}")
        
        if target not in cls._config[instruction_type]:
            cls._config[instruction_type][target] = {}
        
        cls._config[instruction_type][target].update(config)
        logger.info(f"Updated configuration for {instruction_type} post-instruction '{target}'")

# Decorator for registering post-instructions
def post_instruction(name: str, 
                    instruction_type: str = "global", 
                    target: Optional[str] = None,
                    config: Optional[Dict[str, Any]] = None):
    """
    Decorator for registering post-instructions.
    
    Args:
        name: Unique name for the post-instruction
        instruction_type: Type of post-instruction ("model", "family", "endpoint", or "global")
        target: Target identifier (model name, family name, or endpoint name)
        config: Optional configuration for the post-instruction
        
    Returns:
        Callable: Decorator function
    """
    def decorator(func: PostInstructionFn) -> PostInstructionFn:
        PostInstructionManager.register_post_instruction(
            post_instruction_fn=func,
            name=name,
            instruction_type=instruction_type,
            target=target,
            config=config
        )
        return func
    return decorator
