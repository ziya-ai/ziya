"""
Improved escape sequence handling for diff application.

This module provides enhanced functions for handling escape sequences in diffs,
particularly focusing on escape sequences with trailing whitespace that can cause
issues with diff application.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple, Union

logger = logging.getLogger(__name__)

def clean_escape_sequences_in_diff(diff_content: str) -> str:
    """
    Clean escape sequences in a diff to handle trailing whitespace issues.
    
    Args:
        diff_content: The diff content to clean
        
    Returns:
        The cleaned diff content
    """
    if not diff_content:
        return diff_content
    
    # Process the diff line by line
    lines = diff_content.splitlines(True)  # Keep line endings
    result_lines = []
    
    for line in lines:
        # Process lines with escape sequences and trailing whitespace
        cleaned_line = handle_escape_sequence_line(line)
        result_lines.append(cleaned_line)
    
    return ''.join(result_lines)

def handle_escape_sequence_line(line: str) -> str:
    """
    Handle escape sequences in a line, particularly focusing on method calls with
    escape sequences that have trailing whitespace.
    
    Args:
        line: The line to process
        
    Returns:
        The processed line
    """
    if not line:
        return line
    
    # Pattern to match method calls with escape sequences and trailing whitespace
    # This pattern looks for:
    # 1. A method call like .replace(/pattern/g, 'replacement')
    # 2. With trailing whitespace after the closing parenthesis
    pattern = r'(\.\w+\([^)]*\\[rnt]\\?[^)]*\))(\s+)([^\S\r\n]*(?:\r?\n|$))'
    
    # Replace with the method call without trailing whitespace
    result = re.sub(pattern, r'\1\3', line)
    
    return result

def preprocess_diff_for_escape_sequences(diff_content: str) -> str:
    """
    Preprocess a diff to handle escape sequences before applying it.
    
    Args:
        diff_content: The diff content to preprocess
        
    Returns:
        The preprocessed diff content
    """
    return clean_escape_sequences_in_diff(diff_content)
