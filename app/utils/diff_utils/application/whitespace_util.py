"""
Utilities for handling whitespace in diffs.

This module provides functions for normalizing whitespace and improving
the comparison of text with different whitespace patterns.
"""

import re
from typing import List, Optional

def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text for comparison purposes.
    
    This function:
    1. Replaces tabs with spaces
    2. Normalizes line endings
    3. Collapses multiple spaces to a single space
    4. Trims leading/trailing whitespace
    
    Args:
        text: The text to normalize
        
    Returns:
        Normalized text with consistent whitespace
    """
    if not text:
        return ""
    
    # Replace tabs with spaces
    text = text.replace('\t', '    ')
    
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Split into lines, trim each line, and rejoin
    lines = text.split('\n')
    lines = [line.strip() for line in lines]
    
    # Remove empty lines
    lines = [line for line in lines if line]
    
    return '\n'.join(lines)

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

def get_whitespace_normalized_lines(lines: List[str]) -> List[str]:
    """
    Normalize whitespace in a list of lines.
    
    Args:
        lines: List of text lines
        
    Returns:
        List of lines with normalized whitespace
    """
    return [normalize_whitespace(line) for line in lines]
