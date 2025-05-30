"""
Utilities for handling empty file changes.
"""

import re
from typing import Optional, List, Tuple
import os

from app.utils.logging_utils import logger
from .newline_handler import detect_line_endings, normalize_line_endings

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
    
    # Determine the appropriate line ending
    # For new files, use the system default
    if os.name == 'nt':  # Windows
        line_ending = '\r\n'
    else:  # Unix/Mac
        line_ending = '\n'
    
    # Join the content with the appropriate line ending
    content = line_ending.join(content_lines)
    
    # Ensure the file doesn't start with an extra newline
    if content.startswith(('\n', '\r\n')):
        if content.startswith('\r\n'):
            content = content[2:]
        else:
            content = content[1:]
    
    # Ensure the file has a final newline if it's not empty
    if content and not content.endswith(('\n', '\r\n')):
        content += line_ending
    
    # Write the content to the file
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    logger.info(f"Successfully applied empty file diff to {file_path}")
    return True

def fix_empty_file_issues(file_path: str, content: str) -> str:
    """
    Fix issues with empty files.
    
    Args:
        file_path: Path to the file
        content: The file content
        
    Returns:
        The fixed content
    """
    # If the file is empty, return an empty string
    if not content:
        return ""
    
    # If the file starts with a newline but has other content, remove the leading newline
    if content.startswith(('\n', '\r\n')):
        logger.info(f"Removing leading newline from {file_path}")
        if content.startswith('\r\n'):
            content = content[2:]
        else:
            content = content[1:]
    
    # Detect the line ending used in the file
    dominant_ending, has_final_newline = detect_line_endings(content)
    
    # Ensure consistent line endings
    content = normalize_line_endings(content, dominant_ending)
    
    # Ensure the file has a final newline if it's not empty
    if content and not content.endswith(('\n', '\r\n')):
        logger.info(f"Adding final newline to {file_path}")
        content += dominant_ending
    
    # Ensure we don't have multiple trailing newlines
    while content.endswith(dominant_ending + dominant_ending):
        content = content[:-len(dominant_ending)]
    
    return content
