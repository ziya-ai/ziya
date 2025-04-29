"""
Comprehensive escape sequence handler for diff application.

This module provides utilities for handling escape sequences in diff application,
ensuring that escape sequences are properly preserved and compared.
"""

import re
import logging
import json
from typing import List, Dict, Any, Optional, Tuple, Union

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
    '\\a': '\a',  # bell
}

# Regular expressions for detecting escape sequences
ESCAPE_REGEX = re.compile(r'\\[nrtbfv\'\"\\]')
UNICODE_ESCAPE_REGEX = re.compile(r'\\u[0-9a-fA-F]{4}')
HEX_ESCAPE_REGEX = re.compile(r'\\x[0-9a-fA-F]{2}')

def contains_escape_sequences(text: str) -> bool:
    """
    Check if the text contains escape sequences.
    
    Args:
        text: The text to check
        
    Returns:
        True if the text contains escape sequences, False otherwise
    """
    if not text or not isinstance(text, str):
        return False
        
    # Look for common escape sequences
    if ESCAPE_REGEX.search(text):
        return True
    
    # Check for Unicode escape sequences like \u1234
    if UNICODE_ESCAPE_REGEX.search(text):
        return True
    
    # Check for hex escape sequences like \x12
    if HEX_ESCAPE_REGEX.search(text):
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
    if not text or not isinstance(text, str):
        return "" if text is None else str(text)
    
    if preserve_literals:
        # When preserving literals, we don't convert escape sequences
        # This is important for code comparison where we want to compare the literal text
        return text
    
    # Convert escape sequences to their actual characters
    result = text
    
    # First handle literal backslash followed by escape character
    # This is a special case for strings like "\\n" which should be treated as "\n"
    i = 0
    while i < len(result):
        for escaped, unescaped in ESCAPE_SEQUENCES.items():
            if i + len(escaped) <= len(result) and result[i:i+len(escaped)] == escaped:
                # Check if this is part of a larger escape sequence
                if i > 0 and result[i-1] == '\\':
                    i += 1
                    continue
                # Replace the escape sequence
                result = result[:i] + unescaped + result[i+len(escaped):]
                i += 1
                break
        else:
            i += 1
    
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
    return [normalize_line_for_comparison(line) for line in lines]

def normalize_line_for_comparison(line: str) -> str:
    """
    Normalize a single line for comparison, handling escape sequences and whitespace.
    
    Args:
        line: The line to normalize
        
    Returns:
        The normalized line
    """
    if not line:
        return ""
        
    # Preserve escape sequences but normalize whitespace
    result = line.rstrip()
    
    # Handle invisible Unicode characters
    result = normalize_invisible_unicode(result)
    
    return result

def normalize_invisible_unicode(text: str) -> str:
    """
    Normalize invisible Unicode characters for consistent comparison.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    if not text:
        return ""
        
    # Remove zero-width spaces and other invisible characters
    invisible_chars = [
        '\u200b',  # Zero-width space
        '\u200c',  # Zero-width non-joiner
        '\u200d',  # Zero-width joiner
        '\u2060',  # Word joiner
        '\ufeff',  # Zero-width no-break space
    ]
    
    result = text
    for char in invisible_chars:
        result = result.replace(char, '')
    
    return result

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
    has_escapes = False
    for line in old_lines + new_lines:
        if contains_escape_sequences(line):
            has_escapes = True
            break
            
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
    
    # Check for special case: text append operations
    if any('+=' in line for line in new_lines):
        result = handle_text_append_operations(result, hunk, actual_position)
    # Check for special case: JSON string with escape sequences
    elif any('JSON' in line or 'json' in line for line in new_lines):
        result = handle_json_escape_sequences(result, hunk, actual_position)
    else:
        # Standard application with escape sequence preservation
        result[actual_position:end_pos] = new_lines
    
    return result

def handle_text_append_operations(file_lines: List[str], hunk: Dict[str, Any], position: int) -> List[str]:
    """
    Handle text append operations with escape sequences.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        position: The position to apply the hunk
        
    Returns:
        The modified file lines
    """
    import re
    
    result = file_lines.copy()
    new_lines = hunk.get('new_lines', [])
    old_lines = []
    
    for line in hunk.get('old_block', []):
        if isinstance(line, str) and (line.startswith('-') or line.startswith(' ')):
            old_lines.append(line[1:])
    
    # Extract all text append operations
    append_operations = []
    for line in new_lines:
        if '+=' in line:
            append_match = re.match(r'^(\s*)([a-zA-Z0-9_]+)\s*\+=\s*(.+)$', line)
            if append_match:
                indentation, var_name, value = append_match.groups()
                append_operations.append((indentation, var_name, value))
    
    # If no append operations found, do standard replacement
    if not append_operations:
        end_pos = min(position + len(old_lines), len(result))
        result[position:end_pos] = new_lines
        return result
    
    # Process each append operation
    for indentation, var_name, value in append_operations:
        # Check if this append operation is already in the file
        found = False
        for i, file_line in enumerate(result):
            if f"{var_name} +=" in file_line:
                file_append_match = re.match(r'^(\s*)([a-zA-Z0-9_]+)\s*\+=\s*(.+)$', file_line)
                if file_append_match and file_append_match.group(2) == var_name:
                    file_value = file_append_match.group(3)
                    if normalize_escape_sequences(file_value).strip() == normalize_escape_sequences(value).strip():
                        found = True
                        break
        
        if not found:
            # Find the appropriate position to add this line
            # Look for the variable declaration
            var_pos = -1
            for i, file_line in enumerate(result):
                if f"{var_name} =" in file_line:
                    var_pos = i
                    break
            
            if var_pos >= 0:
                # Add the append operation after the variable declaration
                new_line = f"{indentation}{var_name} += {value}"
                if not new_line.endswith('\n'):
                    new_line += '\n'
                result.insert(var_pos + 1, new_line)
            else:
                # Couldn't find the variable declaration, add at the end
                new_line = f"{indentation}{var_name} += {value}"
                if not new_line.endswith('\n'):
                    new_line += '\n'
                result.append(new_line)
    
    # Apply the remaining non-append lines
    non_append_lines = [line for line in new_lines if '+=' not in line]
    if non_append_lines:
        # Find the appropriate position to add these lines
        # Use the position of the first append operation as a reference
        if append_operations:
            var_name = append_operations[0][1]
            var_pos = -1
            for i, file_line in enumerate(result):
                if f"{var_name} =" in file_line:
                    var_pos = i
                    break
            
            if var_pos >= 0:
                # Add the non-append lines after the variable declaration
                for line in reversed(non_append_lines):
                    result.insert(var_pos + 1, line)
    
    return result

def handle_json_escape_sequences(file_lines: List[str], hunk: Dict[str, Any], position: int) -> List[str]:
    """
    Handle JSON string content with escape sequences.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        position: The position to apply the hunk
        
    Returns:
        The modified file lines
    """
    result = file_lines.copy()
    new_lines = hunk.get('new_lines', [])
    
    # Extract the old lines
    old_lines = []
    for line in hunk.get('old_block', []):
        if isinstance(line, str) and (line.startswith('-') or line.startswith(' ')):
            old_lines.append(line[1:])
    
    # Apply the hunk, preserving the structure of JSON strings
    end_pos = min(position + len(old_lines), len(result))
    
    # Special handling for JSON strings with escape sequences
    # Preserve the indentation and structure of the JSON string
    result[position:end_pos] = new_lines
    
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

# Backward compatibility functions
def normalize_escape_sequences_for_diff(text: str) -> str:
    """
    Backward compatibility with escape_handler.py.
    
    Args:
        text: The text to normalize
        
    Returns:
        The normalized text
    """
    return normalize_escape_sequences(text, preserve_literals=False)

def handle_escape_sequences_legacy(original_content: str, git_diff: str) -> Optional[str]:
    """
    Backward compatibility with escape_handling.py.
    
    Args:
        original_content: The original content
        git_diff: The git diff to apply
        
    Returns:
        The modified content or None if no changes were needed
    """
    # Check if this diff contains escape sequences that need special handling
    if not contains_escape_sequences(git_diff):
        return None
    
    # Split the content into lines
    original_lines = original_content.splitlines(True)
    
    # Parse the diff to extract hunks
    hunks = parse_diff_hunks(git_diff)
    
    # Apply each hunk
    modified_lines = original_lines.copy()
    for hunk in hunks:
        start_line = hunk['old_start'] - 1  # 0-indexed
        modified_lines = handle_escape_sequences_in_hunk(modified_lines, hunk, start_line)
    
    # Join the lines back into a single string
    return ''.join(modified_lines)
