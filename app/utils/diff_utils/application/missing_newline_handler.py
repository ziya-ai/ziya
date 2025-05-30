"""
Utilities for handling missing newline at end of file issues in diffs.
"""

import re
from typing import Optional

from app.utils.logging_utils import logger

def has_missing_newline_marker(diff_content: str) -> bool:
    """
    Check if the diff contains a missing newline marker.
    
    Args:
        diff_content: The diff content to check
        
    Returns:
        True if the diff contains a missing newline marker, False otherwise
    """
    return "\\ No newline at end of file" in diff_content

def process_missing_newline_issues(original_content: str, diff_content: str) -> Optional[str]:
    """
    Process missing newline at end of file issues in a diff.
    
    Args:
        original_content: The original file content
        diff_content: The diff content to apply
        
    Returns:
        The processed content or None if no processing was needed
    """
    if not has_missing_newline_marker(diff_content):
        return None
        
    logger.info("Processing missing newline at end of file")
    
    # Check if the original file ends with a newline
    original_has_newline = original_content.endswith('\n')
    
    # Check if the diff adds or removes a newline
    adds_newline = "\\ No newline at end of file" in diff_content and ("+\n" in diff_content or not original_has_newline)
    removes_newline = "\\ No newline at end of file" in diff_content and ("-\n" in diff_content or original_has_newline)
    
    # Apply the appropriate change
    if adds_newline and not original_has_newline:
        return original_content + '\n'
    elif removes_newline and original_has_newline:
        return original_content.rstrip('\n')
    
    # No change needed
    return None
