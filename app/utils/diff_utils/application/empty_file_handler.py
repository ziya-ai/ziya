"""
Utilities for handling empty file changes.
"""

import re
from typing import Optional

from app.utils.logging_utils import logger

def is_empty_file_diff(diff_content: str) -> bool:
    """
    Check if a diff is for an empty file.
    
    Args:
        diff_content: The diff content to check
        
    Returns:
        True if the diff is for an empty file
    """
    # Check if the diff starts with an empty file
    lines = diff_content.splitlines()
    
    # Look for patterns that indicate an empty file diff
    if len(lines) >= 3:
        # Check if the diff is adding content to an empty file
        if lines[2] == '@@ -1,0 +1,' or lines[2].startswith('@@ -0,0 +1,'):
            return True
    
    return False

def handle_empty_file_diff(file_path: str, diff_content: str) -> Optional[bool]:
    """
    Handle a diff for an empty file.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        
    Returns:
        True if the diff was handled, None otherwise
    """
    if not is_empty_file_diff(diff_content):
        return None
    
    logger.info(f"Handling empty file diff for {file_path}")
    
    # Extract the content to add
    content_lines = []
    for line in diff_content.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            content_lines.append(line[1:])
    
    # Write the content to the file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(content_lines))
    
    logger.info(f"Successfully applied empty file diff to {file_path}")
    return True
