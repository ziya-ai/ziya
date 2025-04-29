"""
Integration with language handlers for diff application.
"""

from typing import Tuple, Optional, Dict, Any
import os

from app.utils.logging_utils import logger

def verify_changes_with_language_handler(file_path: str, original_content: str, modified_content: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verify changes using the appropriate language handler.
    
    Args:
        file_path: Path to the file
        original_content: Original file content
        modified_content: Modified file content
        
    Returns:
        Tuple of (is_valid, error_details)
    """
    try:
        from ..language_handlers import LanguageHandlerRegistry
        
        # Get the appropriate handler for the file
        handler_class = LanguageHandlerRegistry.get_handler(file_path)
        
        # Verify the changes
        is_valid, error_msg = handler_class.verify_changes(original_content, modified_content, file_path)
        
        if not is_valid:
            logger.warning(f"Language validation failed for {file_path}: {error_msg}")
            return False, {"type": "language_validation", "message": error_msg}
        
        # Check for duplicates
        has_duplicates, duplicates = handler_class.detect_duplicates(original_content, modified_content)
        
        if has_duplicates:
            logger.warning(f"Duplicate code detected in {file_path}: {', '.join(duplicates)}")
            return False, {"type": "duplicate_code", "duplicates": duplicates}
        
        return True, None
    except ImportError:
        # Language handlers not available, continue without validation
        logger.debug("Language handlers not available for validation")
        return True, None
    except Exception as e:
        # Log the error but continue with the application
        logger.warning(f"Error in language validation: {str(e)}")
        return True, None  # Don't block the application for validation errors
