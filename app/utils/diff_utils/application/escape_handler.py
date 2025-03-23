"""
Utilities for handling escape sequences in diffs.
"""

import re
from typing import List, Dict, Any, Optional

def normalize_escape_sequences_for_diff(text: str) -> str:
    """
    Normalize escape sequences in text for diff comparison.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    # Replace common escape sequences with their actual characters for comparison
    replacements = {
        '\\n': '\n',
        '\\r': '\r',
        '\\t': '\t',
        '\\"': '"',
        "\\'": "'",
        '\\\\': '\\'
    }
    
    result = text
    for esc, char in replacements.items():
        result = result.replace(esc, char)
    
    return result

def handle_escape_sequences_in_hunk(file_lines: List[str], hunk: Dict[str, Any], position: int) -> List[str]:
    """
    Handle escape sequences in a hunk.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        position: The position to apply the hunk
        
    Returns:
        The modified file lines
    """
    # Extract the old and new lines
    old_lines = []
    for line in hunk['old_block']:
        if line.startswith('-') or line.startswith(' '):
            old_lines.append(line[1:])
    
    new_lines = hunk['new_lines']
    
    # Check if this hunk contains escape sequences
    has_escapes = any('\\' in line for line in old_lines + new_lines)
    if not has_escapes:
        return file_lines
    
    # Normalize escape sequences in the file lines
    normalized_file = [normalize_escape_sequences_for_diff(line) for line in file_lines]
    normalized_old = [normalize_escape_sequences_for_diff(line) for line in old_lines]
    normalized_new = [normalize_escape_sequences_for_diff(line) for line in new_lines]
    
    # Find the best position for the hunk
    best_pos = position
    best_match = 0
    
    for i in range(max(0, position - 10), min(len(normalized_file), position + 10)):
        if i + len(normalized_old) <= len(normalized_file):
            matches = sum(1 for j in range(len(normalized_old)) 
                         if normalized_file[i+j].rstrip() == normalized_old[j].rstrip())
            if matches > best_match:
                best_match = matches
                best_pos = i
    
    # Apply the changes
    if best_match > 0:
        result = file_lines.copy()
        result[best_pos:best_pos + len(old_lines)] = new_lines
        return result
    
    return file_lines
