"""
Escape sequence handler for diff application.

This module provides functions for handling escape sequences in diffs,
particularly focusing on escape sequences that can cause issues with
diff application.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from ..core.escape_utils import (
    normalize_escape_sequences,
    contains_escape_sequences,
    handle_json_escape_sequences
)

logger = logging.getLogger(__name__)

def handle_escape_sequences(original_content: str, git_diff: str) -> Optional[str]:
    """
    Handle escape sequences in a diff.
    This is a general-purpose handler that works for any file with escape sequences.
    
    Args:
        original_content: The original content
        git_diff: The git diff to apply
        
    Returns:
        The modified content with escape sequences handled properly, or None if no handling needed
    """
    # Check if the diff contains escape sequences
    if not contains_escape_sequences(git_diff) and not contains_escape_sequences(original_content):
        return None
    
    logger.debug("Handling escape sequences in diff")
    
    # Use the standard difflib apply function to apply the diff
    # This will handle the basic diff application
    from ..application.patch_apply import apply_diff_with_difflib
    
    try:
        # First try to apply the diff normally
        modified_content = apply_diff_with_difflib(original_content, git_diff)
        
        # Check if the result contains escape sequences
        if contains_escape_sequences(modified_content):
            logger.debug("Modified content contains escape sequences")
            
            # Normalize escape sequences in the result
            modified_content = normalize_escape_sequences(modified_content, preserve_literals=True)
            
        return modified_content
    except Exception as e:
        logger.error(f"Error handling escape sequences: {str(e)}")
        # Fall back to standard handling
        return None

def handle_json_escape_sequences(original_content: str, git_diff: str) -> Optional[str]:
    """
    Handle JSON escape sequences in a diff.
    This is a specialized handler for JSON files.
    
    Args:
        original_content: The original content
        git_diff: The git diff to apply
        
    Returns:
        The modified content with JSON escape sequences handled properly, or None if no handling needed
    """
    # Check if this looks like a JSON file
    if not (original_content.strip().startswith('{') and original_content.strip().endswith('}')) and \
       not (original_content.strip().startswith('[') and original_content.strip().endswith(']')):
        return None
    
    logger.debug("Handling JSON escape sequences in diff")
    
    # Use the standard difflib apply function to apply the diff
    # This will handle the basic diff application
    from ..application.patch_apply import apply_diff_with_difflib
    
    try:
        # First try to apply the diff normally
        modified_content = apply_diff_with_difflib(original_content, git_diff)
        
        # For JSON files, we need to be careful about escape sequences
        # Try to parse the JSON to validate it
        import json
        try:
            json.loads(modified_content)
            logger.debug("Modified JSON content is valid")
        except json.JSONDecodeError as e:
            logger.warning(f"Modified JSON content is invalid: {str(e)}")
            # Try to fix common JSON escape sequence issues
            modified_content = fix_json_escape_sequences(modified_content)
            
        return modified_content
    except Exception as e:
        logger.error(f"Error handling JSON escape sequences: {str(e)}")
        # Fall back to standard handling
        return None

def fix_json_escape_sequences(content: str) -> str:
    """
    Fix common JSON escape sequence issues.
    
    Args:
        content: The JSON content to fix
        
    Returns:
        The fixed JSON content
    """
    # Fix common JSON escape sequence issues
    result = content
    
    # Fix unescaped quotes in strings
    result = re.sub(r'(?<!\\)"([^"]*)"', r'"\1"', result)
    
    # Fix unescaped backslashes
    result = re.sub(r'(?<!\\)\\(?!["\\bfnrt])', r'\\\\', result)
    
    # Fix unescaped control characters
    result = re.sub(r'[\x00-\x1F]', lambda m: f'\\u{ord(m.group(0)):04x}', result)
    
    return result
