"""
Specialized handler for whitespace changes in diffs.

This module provides functions for detecting and handling whitespace-only changes
in diffs, improving the robustness of the diff application pipeline.
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple

# Configure logging
logger = logging.getLogger(__name__)

def is_whitespace_only_diff(hunk: Dict[str, Any]) -> bool:
    """
    Check if a hunk contains only whitespace changes.
    
    Args:
        hunk: The hunk to check
        
    Returns:
        True if the hunk only contains whitespace changes, False otherwise
    """
    # Extract the removed and added lines
    removed_lines = []
    added_lines = []
    
    # Extract from old_block and new_block
    if 'old_block' in hunk and 'new_block' in hunk:
        for line in hunk.get('old_block', []):
            if line.startswith('-'):
                removed_lines.append(line[1:])
        for line in hunk.get('new_block', []):
            if line.startswith('+'):
                added_lines.append(line[1:])
    
    # If no changes, not a whitespace-only change
    if not removed_lines and not added_lines:
        return False
    
    # Special case: empty lines being added or removed
    if all(not line.strip() for line in removed_lines) or all(not line.strip() for line in added_lines):
        return True
    
    # If different number of non-empty lines, not just whitespace
    non_empty_removed = [line for line in removed_lines if line.strip()]
    non_empty_added = [line for line in added_lines if line.strip()]
    
    if len(non_empty_removed) != len(non_empty_added):
        return False
    
    # Compare the non-whitespace content of each pair
    for removed, added in zip(non_empty_removed, non_empty_added):
        if re.sub(r'\s+', '', removed) != re.sub(r'\s+', '', added):
            return False
    
    return True

def normalize_whitespace_for_comparison(text: str) -> str:
    """
    Normalize whitespace in text for comparison purposes.
    
    Args:
        text: The text to normalize
        
    Returns:
        Normalized text with consistent whitespace
    """
    # Replace tabs with spaces
    normalized = text.replace('\t', '    ')
    
    # Normalize line endings
    normalized = normalized.replace('\r\n', '\n').replace('\r', '\n')
    
    # Collapse multiple spaces to a single space
    normalized = re.sub(r' +', ' ', normalized)
    
    # Trim leading/trailing whitespace
    normalized = normalized.strip()
    
    return normalized

def compare_ignoring_whitespace(text1: str, text2: str) -> bool:
    """
    Compare two text strings ignoring whitespace differences.
    
    Args:
        text1: First text to compare
        text2: Second text to compare
        
    Returns:
        True if the texts are equivalent ignoring whitespace, False otherwise
    """
    # Remove all whitespace and compare
    text1_no_ws = re.sub(r'\s+', '', text1)
    text2_no_ws = re.sub(r'\s+', '', text2)
    
    return text1_no_ws == text2_no_ws
