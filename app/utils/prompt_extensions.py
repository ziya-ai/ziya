"""
Prompt Extensions Framework for Ziya.

This module provides a modular framework for extending prompts based on model, model family, or endpoint.
Extensions can be registered and applied to prompts dynamically.
"""

import os
from typing import Dict, List, Optional, Callable, Any, Union
from app.utils.logging_utils import logger

# Type definitions
PromptExtensionFn = Callable[[str, Dict[str, Any]], str]
PromptExtensionConfig = Dict[str, Any]

class PromptExtensionManager:
    """
    Manager for prompt extensions that can be applied based on model, model family, or endpoint.
    """
    
    # Class-level storage for extensions
    _extensions: Dict[str, Dict[str, PromptExtensionFn]] = {
        "model": {},       # Extensions for specific models
        "family": {},      # Extensions for model families
        "endpoint": {},    # Extensions for endpoints
        "global": {}       # Global extensions applied to all prompts
    }
    
    # Extension configuration
    _config: Dict[str, Dict[str, PromptExtensionConfig]] = {
        "model": {},
        "family": {},
        "endpoint": {},
        "global": {}
    }
    
    @classmethod
    def register_extension(cls, 
                          extension_fn: PromptExtensionFn, 
                          name: str, 
                          extension_type: str = "global", 
                          target: Optional[str] = None,
                          config: Optional[Dict[str, Any]] = None) -> None:
        """
        Register a prompt extension function.
        
        Args:
            extension_fn: Function that takes a prompt and context and returns a modified prompt
            name: Unique name for the extension
            extension_type: Type of extension ("model", "family", "endpoint", or "global")
            target: Target identifier (model name, family name, or endpoint name)
            config: Optional configuration for the extension
        """
        if extension_type not in cls._extensions:
            raise ValueError(f"Invalid extension type: {extension_type}. Must be one of: {', '.join(cls._extensions.keys())}")
        
        # For non-global extensions, target is required
        if extension_type != "global" and not target:
            raise ValueError(f"Target is required for {extension_type} extensions")
        
        # Create a unique key for the extension
        key = target if target else name
        
        # Register the extension
        cls._extensions[extension_type][key] = extension_fn
        
        # Store the configuration
        if config:
            if key not in cls._config[extension_type]:
                cls._config[extension_type][key] = {}
            cls._config[extension_type][key] = config
        
        logger.debug(f"Registered prompt extension '{name}' for {extension_type}{': ' + target if target else ''}")
    
    @classmethod
    def apply_extensions(cls, 
                        prompt: str, 
                        model_name: Optional[str] = None, 
                        model_family: Optional[str] = None,
                        endpoint: Optional[str] = None,
                        context: Optional[Dict[str, Any]] = None) -> str:
        """
        Apply all relevant extensions to a prompt.
        
        Args:
            prompt: The original prompt
            model_name: Name of the model
            model_family: Family of the model
            endpoint: Endpoint being used
            context: Additional context for extensions
            
        Returns:
            str: The modified prompt
        """
        if context is None:
            context = {}
        
        logger.debug(f"Applying extensions to prompt length: {len(prompt)}")
        
        # Start with the original prompt
        modified_prompt = prompt
        
        # Apply global extensions first
        for name, extension_fn in cls._extensions["global"].items():
            extension_context = {**context, "config": cls._config["global"].get(name, {})}
            try:
                # Check if the extension is enabled
                if not extension_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled global extension '{name}'")
                    continue
                    
                modified_prompt = extension_fn(modified_prompt, extension_context)
                logger.debug(f"Applied global extension '{name}' - new length: {len(modified_prompt)}")
                logger.info(f"Applied global extension '{name}' - new length: {len(modified_prompt)}")
                logger.debug(f"Applied global extension '{name}'")
            except Exception as e:
                logger.error(f"Error applying global extension '{name}': {e}")
        
        # Apply endpoint extensions if endpoint is specified
        if endpoint and endpoint in cls._extensions["endpoint"]:
            extension_fn = cls._extensions["endpoint"][endpoint]
            extension_context = {**context, "config": cls._config["endpoint"].get(endpoint, {})}
            try:
                # Check if the extension is enabled
                if not extension_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled endpoint extension for '{endpoint}'")
                else:
                    modified_prompt = extension_fn(modified_prompt, extension_context)
                    logger.info(f"Applied endpoint extension for '{endpoint}' - new length: {len(modified_prompt)}")
                    logger.debug(f"Applied endpoint extension for '{endpoint}' - new length: {len(modified_prompt)}")
                    logger.debug(f"Applied endpoint extension for '{endpoint}'")
            except Exception as e:
                logger.error(f"Error applying endpoint extension for '{endpoint}': {e}")
        
        # Apply family extensions if family is specified
        if model_family and model_family in cls._extensions["family"]:
            extension_fn = cls._extensions["family"][model_family]
            extension_context = {**context, "config": cls._config["family"].get(model_family, {})}
            try:
                # Check if the extension is enabled
                if not extension_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled family extension for '{model_family}'")
                else:
                    modified_prompt = extension_fn(modified_prompt, extension_context)
                    logger.info(f"Applied family extension for '{model_family}' - new length: {len(modified_prompt)}")
                    logger.debug(f"Applied family extension for '{model_family}' - new length: {len(modified_prompt)}")
                    logger.debug(f"Applied family extension for '{model_family}'")
            except Exception as e:
                logger.error(f"Error applying family extension for '{model_family}': {e}")
        
        # Apply model extensions if model is specified
        if model_name and model_name in cls._extensions["model"]:
            extension_fn = cls._extensions["model"][model_name]
            extension_context = {**context, "config": cls._config["model"].get(model_name, {})}
            try:
                # Check if the extension is enabled
                if not extension_context.get("config", {}).get("enabled", True):
                    logger.debug(f"Skipping disabled model extension for '{model_name}'")
                else:
                    modified_prompt = extension_fn(modified_prompt, extension_context)
                    logger.info(f"Applied model extension for '{model_name}' - new length: {len(modified_prompt)}")
                    logger.debug(f"Applied model extension for '{model_name}' - new length: {len(modified_prompt)}")
                    logger.debug(f"Applied model extension for '{model_name}'")
            except Exception as e:
                logger.error(f"Error applying model extension for '{model_name}': {e}")
        
        logger.debug(f"Final modified prompt length: {len(modified_prompt)}")
        return modified_prompt
    
    @classmethod
    def get_extension_config(cls, 
                           extension_type: str, 
                           target: str) -> Optional[Dict[str, Any]]:
        """
        Get the configuration for an extension.
        
        Args:
            extension_type: Type of extension
            target: Target identifier
            
        Returns:
            Optional[Dict[str, Any]]: The extension configuration or None if not found
        """
        if extension_type not in cls._config:
            return None
        
        return cls._config[extension_type].get(target)
    
    @classmethod
    def update_extension_config(cls,
                              extension_type: str,
                              target: str,
                              config: Dict[str, Any]) -> None:
        """
        Update the configuration for an extension.
        
        Args:
            extension_type: Type of extension
            target: Target identifier
            config: New configuration
        """
        if extension_type not in cls._config:
            raise ValueError(f"Invalid extension type: {extension_type}")
        
        if target not in cls._config[extension_type]:
            cls._config[extension_type][target] = {}
        
        cls._config[extension_type][target].update(config)
        logger.info(f"Updated configuration for {extension_type} extension '{target}'")
    
    @classmethod
    def load_extensions_from_directory(cls, directory_path: str) -> None:
        """
        Load extensions from Python files in a directory.
        
        Args:
            directory_path: Path to the directory containing extension files
        """
        import importlib.util
        import sys
        from pathlib import Path
        
        # Create the directory if it doesn't exist
        extension_dir = Path(directory_path)
        if not extension_dir.exists():
            extension_dir.mkdir(parents=True)
            logger.info(f"Created extensions directory: {directory_path}")
            return
        
        # Load each Python file in the directory
        for file_path in extension_dir.glob("*.py"):
            if file_path.name.startswith("_"):
                continue
                
            try:
                # Load the module
                module_name = f"ziya_extensions.{file_path.stem}"
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec is None or spec.loader is None:
                    logger.error(f"Failed to load extension file: {file_path}")
                    continue
                    
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Check if the module has a register_extensions function (optional)
                if hasattr(module, "register_extensions"):
                    module.register_extensions(cls)
                
                logger.debug(f"Loaded extensions from {file_path}")
            except Exception as e:
                logger.error(f"Error loading extension file {file_path}: {e}")

# Decorator for registering extensions
def prompt_extension(name: str, 
                    extension_type: str = "global", 
                    target: Optional[str] = None,
                    config: Optional[Dict[str, Any]] = None):
    """
    Decorator for registering prompt extensions.
    
    Args:
        name: Unique name for the extension
        extension_type: Type of extension ("model", "family", "endpoint", or "global")
        target: Target identifier (model name, family name, or endpoint name)
        config: Optional configuration for the extension
        
    Returns:
        Callable: Decorator function
    """
    def decorator(func: PromptExtensionFn) -> PromptExtensionFn:
        PromptExtensionManager.register_extension(
            extension_fn=func,
            name=name,
            extension_type=extension_type,
            target=target,
            config=config
        )
        return func
    return decorator
