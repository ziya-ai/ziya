"""
Utilities for handling escape sequences in diffs.

This module provides functions for normalizing and handling escape sequences
in diff applications, ensuring that escape sequences are properly preserved
and compared regardless of the specific use case.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("ZIYA")

# Common escape sequences in programming languages
ESCAPE_SEQUENCES = {
    '\\n': '\n',  # newline
    '\\r': '\r',  # carriage return
    '\\t': '\t',  # tab
    '\\b': '\b',  # backspace
    '\\f': '\f',  # form feed
    '\\v': '\v',  # vertical tab
    '\\"': '"',   # double quote
    "\\'": "'",   # single quote
    '\\\\': '\\', # backslash
}

def contains_escape_sequences(text: str) -> bool:
    """
    Check if the text contains escape sequences.
    
    Args:
        text: The text to check
        
    Returns:
        True if the text contains escape sequences, False otherwise
    """
    # Look for common escape sequences
    for esc in ESCAPE_SEQUENCES.keys():
        if esc in text:
            return True
    
    # Also check for Unicode escape sequences like \u1234
    if re.search(r'\\u[0-9a-fA-F]{4}', text):
        return True
    
    # Check for hex escape sequences like \x12
    if re.search(r'\\x[0-9a-fA-F]{2}', text):
        return True
    
    return False

def normalize_escape_sequences(text: str, preserve_literals: bool = True) -> str:
    """
    Normalize escape sequences in text for consistent comparison.
    
    Args:
        text: The text to normalize
        preserve_literals: If True, preserve escape sequences as literals
                          If False, convert escape sequences to their actual characters
        
    Returns:
        The normalized text
    """
    if not text:
        return ""
    
    if preserve_literals:
        # When preserving literals, we don't convert escape sequences
        # This is important for code comparison where we want to compare the literal text
        return text
    
    # Convert escape sequences to their actual characters
    result = text
    for esc, char in ESCAPE_SEQUENCES.items():
        result = result.replace(esc, char)
    
    # Handle Unicode escape sequences
    def replace_unicode(match):
        code = int(match.group(1), 16)
        return chr(code)
    
    result = re.sub(r'\\u([0-9a-fA-F]{4})', replace_unicode, result)
    
    # Handle hex escape sequences
    def replace_hex(match):
        code = int(match.group(1), 16)
        return chr(code)
    
    result = re.sub(r'\\x([0-9a-fA-F]{2})', replace_hex, result)
    
    return result

def normalize_lines_for_comparison(lines: List[str]) -> List[str]:
    """
    Normalize a list of lines for comparison, handling escape sequences.
    
    Args:
        lines: The lines to normalize
        
    Returns:
        The normalized lines
    """
    return [normalize_escape_sequences(line, preserve_literals=True) for line in lines]

def handle_escape_sequences_in_hunk(file_lines: List[str], hunk: Dict[str, Any], position: int) -> List[str]:
    """
    Handle escape sequences in a hunk during diff application.
    
    This function ensures that escape sequences are properly preserved when
    applying a diff to text that contains escape sequences.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        position: The position to apply the hunk
        
    Returns:
        The modified file lines
    """
    # Extract the old and new lines
    old_lines = []
    for line in hunk.get('old_block', []):
        if isinstance(line, str) and (line.startswith('-') or line.startswith(' ')):
            old_lines.append(line[1:])
    
    new_lines = hunk.get('new_lines', [])
    
    # Check if this hunk contains escape sequences
    has_escapes = any(contains_escape_sequences(line) for line in old_lines + new_lines)
    if not has_escapes:
        # No escape sequences, use standard application
        return file_lines
    
    logger.debug(f"Hunk contains escape sequences, using specialized handling")
    
    # Create a copy of the file lines to modify
    result = file_lines.copy()
    
    # Determine the actual position to apply the hunk
    actual_position = position
    
    # Apply the hunk, preserving escape sequences
    end_pos = min(actual_position + len(old_lines), len(result))
    
    # Check if the hunk is already applied
    if end_pos - actual_position == len(new_lines):
        # Compare the content at the position with the new lines
        current_content = result[actual_position:end_pos]
        normalized_current = normalize_lines_for_comparison(current_content)
        normalized_new = normalize_lines_for_comparison(new_lines)
        
        if normalized_current == normalized_new:
            logger.debug("Hunk already applied, no changes needed")
            return result
    
    # Apply the hunk
    result[actual_position:end_pos] = new_lines
    
    return result

def apply_escape_sequence_fixes(original_content: str, modified_content: str, diff_content: str) -> str:
    """
    Apply fixes for escape sequences in the modified content.
    
    This function is used as a post-processing step after applying a diff
    to ensure that escape sequences are properly preserved.
    
    Args:
        original_content: The original content
        modified_content: The modified content after applying the diff
        diff_content: The diff content that was applied
        
    Returns:
        The fixed modified content
    """
    # Check if the diff contains escape sequences
    if not contains_escape_sequences(diff_content):
        return modified_content
    
    logger.debug("Applying escape sequence fixes")
    
    # Split the content into lines
    original_lines = original_content.splitlines(True)
    modified_lines = modified_content.splitlines(True)
    
    # Parse the diff to extract hunks
    hunks = parse_diff_hunks(diff_content)
    
    # Apply fixes for each hunk
    for hunk in hunks:
        # Get the lines affected by the hunk
        start_line = hunk['old_start'] - 1  # 0-indexed
        end_line = start_line + hunk['old_count']
        
        # Get the original and modified text for the affected lines
        original_text = ''.join(original_lines[start_line:end_line]) if start_line < len(original_lines) else ""
        
        # Check if this hunk contains escape sequences
        if contains_escape_sequences(original_text) or any(contains_escape_sequences(line) for line in hunk['new_lines']):
            # Apply specialized handling for this hunk
            modified_lines = handle_escape_sequences_in_hunk(modified_lines, hunk, start_line)
    
    # Join the lines back into a single string
    return ''.join(modified_lines)

def parse_diff_hunks(diff_content: str) -> List[Dict[str, Any]]:
    """
    Parse a diff into hunks.
    
    Args:
        diff_content: The diff content
        
    Returns:
        A list of hunks
    """
    hunks = []
    lines = diff_content.splitlines()
    
    # Skip header lines
    i = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        i += 1
    
    # Parse each hunk
    while i < len(lines):
        if lines[i].startswith("@@"):
            # Parse hunk header
            match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[i])
            if not match:
                i += 1
                continue
            
            old_start = int(match.group(1))
            old_count = int(match.group(2) or 1)
            new_start = int(match.group(3))
            new_count = int(match.group(4) or 1)
            
            # Extract hunk content
            old_block = []
            new_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@"):
                if lines[i].startswith('-'):
                    old_block.append(lines[i])
                elif lines[i].startswith('+'):
                    new_lines.append(lines[i][1:])
                elif lines[i].startswith(' '):
                    old_block.append(lines[i])
                    new_lines.append(lines[i][1:])
                i += 1
            
            hunks.append({
                'old_start': old_start,
                'old_count': old_count,
                'new_start': new_start,
                'new_count': new_count,
                'old_block': old_block,
                'new_lines': new_lines
            })
        else:
            i += 1
    
    return hunks
