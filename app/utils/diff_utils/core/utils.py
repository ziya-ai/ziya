"""
Core utility functions used throughout the diff_utils package.
"""

import difflib
from typing import List

def clamp(value: int, low: int, high: int) -> int:
    """
    Simple clamp utility to ensure we stay in range.
    
    Args:
        value: The value to clamp
        low: The lower bound
        high: The upper bound
        
    Returns:
        The clamped value
    """
    return max(low, min(high, value))

def normalize_escapes(text: str) -> str:
    """
    Normalize escape sequences in text to improve matching.
    This helps with comparing strings that have different escape sequence representations.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    # Replace common escape sequences with placeholders
    replacements = {
        '\\n': '_NL_',
        '\\r': '_CR_',
        '\\t': '_TAB_',
        '\\"': '_QUOTE_',
        "\\'": '_SQUOTE_',
        '\\\\': '_BSLASH_'
    }
    
    result = text
    for esc, placeholder in replacements.items():
        result = result.replace(esc, placeholder)
    
    return result

def calculate_block_similarity(file_block: List[str], diff_block: List[str]) -> float:
    """
    Calculate similarity between two blocks of text using difflib with improved handling
    of whitespace and special characters.
    
    Args:
        file_block: List of lines from the file
        diff_block: List of lines from the diff
        
    Returns:
        A ratio between 0.0 and 1.0 where 1.0 means identical
    """
    # Handle empty blocks
    if not file_block and not diff_block:
        return 1.0
    if not file_block or not diff_block:
        return 0.0
    
    # Normalize whitespace in both blocks
    file_str = '\n'.join(line.rstrip() for line in file_block)
    diff_str = '\n'.join(line.rstrip() for line in diff_block)
    
    # Use SequenceMatcher for fuzzy matching with improved junk detection
    matcher = difflib.SequenceMatcher(None, file_str, diff_str)
    
    # Get the similarity ratio
    ratio = matcher.ratio()
    
    # For blocks with special characters or escape sequences, do additional checks
    if ratio < 0.9 and (any('\\' in line for line in file_block) or any('\\' in line for line in diff_block)):
        # Try comparing with normalized escape sequences
        norm_file = '\n'.join(normalize_escapes(line) for line in file_block)
        norm_diff = '\n'.join(normalize_escapes(line) for line in diff_block)
        
        norm_matcher = difflib.SequenceMatcher(None, norm_file, norm_diff)
        norm_ratio = norm_matcher.ratio()
        
        # Use the better ratio
        ratio = max(ratio, norm_ratio)
    
    return ratio
