"""
Shared escape sequence handling utilities for diff processing
Consolidates duplicate implementations across diff_utils modules
"""

import re
from typing import List, Tuple


def normalize_escape_sequences(text: str) -> str:
    """
    Normalize escape sequences in text for consistent comparison.
    Handles common escape patterns like \\n, \\t, \\r, etc.
    
    Consolidated from 5 duplicate implementations.
    """
    if not text:
        return text
    
    # Replace common escape sequences
    replacements = {
        '\\n': '\n',
        '\\t': '\t',
        '\\r': '\r',
        '\\\\': '\\',
        '\\"': '"',
        "\\'": "'",
    }
    
    result = text
    for escaped, actual in replacements.items():
        result = result.replace(escaped, actual)
    
    return result


def contains_escape_sequences(text: str) -> bool:
    """
    Check if text contains escape sequences.
    
    Consolidated from 3 duplicate implementations.
    """
    if not text:
        return False
    
    # Common escape sequence patterns
    escape_patterns = [
        r'\\n', r'\\t', r'\\r', r'\\\\',
        r'\\"', r"\\'", r'\\x[0-9a-fA-F]{2}',
        r'\\u[0-9a-fA-F]{4}', r'\\U[0-9a-fA-F]{8}'
    ]
    
    for pattern in escape_patterns:
        if re.search(pattern, text):
            return True
    
    return False


def apply_escape_sequence_fixes(lines: List[str]) -> List[str]:
    """
    Apply escape sequence normalization to a list of lines.
    
    Consolidated from 3 duplicate implementations.
    """
    return [normalize_escape_sequences(line) for line in lines]


def handle_escape_sequences_in_hunk(hunk_lines: List[str]) -> List[str]:
    """
    Process escape sequences in diff hunk lines.
    Preserves diff markers (+, -, space) while normalizing content.
    
    Consolidated from 3 duplicate implementations.
    """
    processed = []
    
    for line in hunk_lines:
        if not line:
            processed.append(line)
            continue
        
        # Preserve diff marker
        if line[0] in ('+', '-', ' '):
            marker = line[0]
            content = line[1:]
            processed.append(marker + normalize_escape_sequences(content))
        else:
            processed.append(normalize_escape_sequences(line))
    
    return processed


def clean_escape_sequences_in_diff(diff_text: str) -> str:
    """
    Clean escape sequences from entire diff text.
    
    Consolidated from 2 duplicate implementations.
    """
    lines = diff_text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        # Skip diff headers
        if line.startswith(('diff --git', '---', '+++', '@@')):
            cleaned_lines.append(line)
        else:
            cleaned_lines.append(normalize_escape_sequences(line))
    
    return '\n'.join(cleaned_lines)


def handle_escape_sequence_line(line: str, in_hunk: bool = False) -> str:
    """
    Handle escape sequences in a single line.
    
    Consolidated from 2 duplicate implementations.
    """
    if not line or not contains_escape_sequences(line):
        return line
    
    if in_hunk and line and line[0] in ('+', '-', ' '):
        marker = line[0]
        content = normalize_escape_sequences(line[1:])
        return marker + content
    
    return normalize_escape_sequences(line)


def handle_json_escape_sequences(json_text: str) -> str:
    """
    Handle escape sequences specifically in JSON content.
    
    Consolidated from 2 duplicate implementations.
    """
    if not json_text:
        return json_text
    
    # JSON-specific escape handling
    replacements = {
        '\\n': '\n',
        '\\t': '\t',
        '\\r': '\r',
        '\\\\': '\\',
        '\\"': '"',
        '\\/': '/',
        '\\b': '\b',
        '\\f': '\f',
    }
    
    result = json_text
    for escaped, actual in replacements.items():
        result = result.replace(escaped, actual)
    
    return result
