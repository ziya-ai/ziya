"""
Utilities for handling escape sequences in diffs.
"""

import re
from typing import List, Dict, Any

from app.utils.logging_utils import logger

def normalize_escape_sequences(text: str) -> str:
    """
    Normalize escape sequences in text to ensure consistent handling.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    # Replace common escape sequences with their actual characters
    replacements = {
        r'\\n': '\n',
        r'\\r': '\r',
        r'\\t': '\t',
        r'\\"': '"',
        r"\\'": "'",
        r'\\\\': '\\'
    }
    
    result = text
    for pattern, replacement in replacements.items():
        result = result.replace(pattern, replacement)
    
    return result

def handle_escape_sequences(file_path: str, diff_content: str) -> bool:
    """
    Generic handler for diffs containing escape sequences.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        
    Returns:
        True if the diff was successfully applied, False otherwise
    """
    try:
        logger.info(f"Using generic escape sequence handler for {file_path}")
        
        # Read the original file content
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        # Use the core escape handling module
        from ..core.escape_handling import handle_escape_sequences as core_handle_escape_sequences
        modified_content = core_handle_escape_sequences(original_content, diff_content)
        
        if modified_content:
            # Write the modified content
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(modified_content)
                # Ensure the file ends with a newline
                if not modified_content.endswith('\n'):
                    f.write('\n')
            
            logger.info(f"Successfully applied escape sequence changes to {file_path}")
            return True
        else:
            logger.info("No escape sequence changes needed")
            return False
    except Exception as e:
        logger.error(f"Error handling escape sequences: {str(e)}")
        return False
