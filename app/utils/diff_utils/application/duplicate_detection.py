"""
Legacy duplicate detection and syntax validation functions.

These functions are kept for backward compatibility and delegate to the new
language handler architecture.
"""

from typing import Tuple, Optional, Dict, Any

from app.utils.logging_utils import logger
from ..language_handlers import LanguageHandlerRegistry


def verify_no_duplicates(original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Verify that the modified content doesn't contain duplicate functions/methods.
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        file_path: Path to the file
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Get the appropriate language handler for this file
    handler = LanguageHandlerRegistry.get_handler(file_path)
    
    # Check for duplicates using the language-specific handler
    has_duplicates, duplicates = handler.detect_duplicates(original_content, modified_content)
    
    if has_duplicates:
        error_msg = f"Applying diff would create duplicate code: {', '.join(duplicates)}"
        logger.error(error_msg)
        return False, error_msg
    
    return True, None


def check_syntax_validity(original_content: str, modified_content: str, file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Check if the modified content is syntactically valid.
    
    Args:
        original_content: Original file content
        modified_content: Modified file content
        file_path: Path to the file
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Get the appropriate language handler for this file
    handler = LanguageHandlerRegistry.get_handler(file_path)
    
    # Verify changes using the language-specific handler
    return handler.verify_changes(original_content, modified_content, file_path)
