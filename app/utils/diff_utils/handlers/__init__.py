"""
Special case handlers for diff application.

This module provides a registry of special case handlers that can be applied
during the diff application process.
"""

from typing import Dict, Callable, List, Tuple, Optional
import importlib
import pkgutil
import inspect
import re
import os
import sys

from app.utils.logging_utils import logger

# Type definitions for handler functions
HandlerCheckFunc = Callable[[str, str], bool]
HandlerFixFunc = Callable[[str, str, str], str]

class HandlerRegistry:
    """Registry for special case handlers."""
    
    _handlers: List[Tuple[str, HandlerCheckFunc, HandlerFixFunc]] = []
    
    @classmethod
    def register(cls, name: str, check_func: HandlerCheckFunc, fix_func: HandlerFixFunc) -> None:
        """
        Register a new handler.
        
        Args:
            name: Name of the handler
            check_func: Function to check if the handler applies
            fix_func: Function to fix the issue
        """
        cls._handlers.append((name, check_func, fix_func))
        logger.debug(f"Registered special case handler: {name}")
    
    @classmethod
    def get_applicable_handlers(cls, file_path: str, diff_content: str) -> List[Tuple[str, HandlerFixFunc]]:
        """
        Get all handlers that apply to the given file and diff.
        
        Args:
            file_path: Path to the file
            diff_content: The diff content
            
        Returns:
            List of (handler_name, fix_function) tuples
        """
        applicable_handlers = []
        
        for name, check_func, fix_func in cls._handlers:
            try:
                if check_func(file_path, diff_content):
                    applicable_handlers.append((name, fix_func))
                    logger.debug(f"Handler {name} is applicable for {file_path}")
            except Exception as e:
                logger.warning(f"Error checking handler {name}: {str(e)}")
        
        return applicable_handlers
    
    @classmethod
    def apply_handlers(cls, file_path: str, diff_content: str, original_content: str, modified_content: str) -> str:
        """
        Apply all applicable handlers to the content.
        
        Args:
            file_path: Path to the file
            diff_content: The diff content
            original_content: Original file content
            modified_content: Modified file content
            
        Returns:
            The content after applying all handlers
        """
        result = modified_content
        
        applicable_handlers = cls.get_applicable_handlers(file_path, diff_content)
        
        for name, fix_func in applicable_handlers:
            try:
                logger.info(f"Applying handler {name} to {file_path}")
                result = fix_func(file_path, original_content, result)
            except Exception as e:
                logger.warning(f"Error applying handler {name}: {str(e)}")
        
        return result

# Import all handlers in this package
def _import_handlers():
    """Import all handler modules in this package."""
    # Get the directory of this package
    package_dir = os.path.dirname(__file__)
    
    # Import all modules in this package
    for _, module_name, is_pkg in pkgutil.iter_modules([package_dir]):
        if not is_pkg and module_name != "__init__":
            try:
                module = importlib.import_module(f"{__name__}.{module_name}")
                
                # Look for handler functions in the module
                for name, obj in inspect.getmembers(module):
                    if name.startswith("is_") and callable(obj):
                        check_func = obj
                        # Look for the corresponding fix function
                        fix_name = name.replace("is_", "fix_")
                        if hasattr(module, fix_name) and callable(getattr(module, fix_name)):
                            fix_func = getattr(module, fix_name)
                            # Register the handler
                            handler_name = name[3:]  # Remove "is_" prefix
                            HandlerRegistry.register(handler_name, check_func, fix_func)
            except Exception as e:
                logger.warning(f"Error importing handler module {module_name}: {str(e)}")

# Import all handlers
_import_handlers()
