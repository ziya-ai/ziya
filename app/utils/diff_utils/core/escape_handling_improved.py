"""
Improved escape sequence handling utilities for diff application.

This module provides enhanced functions for handling escape sequences in diffs,
particularly focusing on trailing whitespace in escape sequences that can cause
issues with diff application.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple, Set

# Configure logging
logger = logging.getLogger(__name__)

# Common escape sequences in programming languages
COMMON_ESCAPE_SEQUENCES = [
    r'\\n',   # Newline escape
    r'\\r',   # Carriage return escape
    r'\\t',   # Tab escape
    r'\\"',   # Double quote escape
    r"\\'",   # Single quote escape
    r'\\\\',  # Backslash escape
    r'\\b',   # Backspace escape
    r'\\f',   # Form feed escape
    r'\\v',   # Vertical tab escape
    r'\\0',   # Null character escape
    r'\\x[0-9a-fA-F]{2}',  # Hex escape (e.g., \x41)
    r'\\u[0-9a-fA-F]{4}',  # Unicode escape (e.g., \u00A9)
]

# Regular expression patterns for detecting escape sequences in various contexts
ESCAPE_SEQUENCE_PATTERNS = [
    # JavaScript/TypeScript regex literals with escape sequences
    r'(/[^/\\]*(?:\\.[^/\\]*)*/)([gim]*)',
    
    # String literals with escape sequences
    r'(["\'])((?:\\.|[^\\"])*?)\\1',
    
    # Template literals with escape sequences
    r'`([^`\\]*(?:\\.[^`\\]*)*)`',
    
    # Common method calls that process escape sequences
    r'\.replace\([^)]*\\[rnt][^)]*\)',
    r'\.split\([^)]*\\[rnt][^)]*\)',
    r'\.match\([^)]*\\[rnt][^)]*\)',
]

def normalize_escape_sequences(text: str, preserve_literals: bool = True, preserve_trailing_space: bool = False) -> str:
    """
    Normalize escape sequences in text with improved handling for trailing whitespace.
    
    Args:
        text: The text to normalize
        preserve_literals: If True, preserve escape sequences as literals (e.g., '\\n' stays as '\\n')
                          If False, convert escape sequences to their actual characters (e.g., '\\n' becomes a newline)
        preserve_trailing_space: If True, preserve trailing whitespace after escape sequences
                                If False, trim trailing whitespace
        
    Returns:
        The normalized text
    """
    if not text:
        return ""
    
    if preserve_literals:
        # When preserving literals, we don't convert escape sequences
        # This is important for code comparison where we want to compare the literal text
        
        # Special handling for trailing whitespace after escape sequences
        if not preserve_trailing_space:
            # Use a more comprehensive regex to handle trailing whitespace after escape sequences
            # This pattern matches any escape sequence followed by whitespace
            return re.sub(r'(\\[rntbfv0\\\'\"]|\\/|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4})\s+', r'\1', text)
        
        return text
    
    # Common escape sequences to normalize when not preserving literals
    escape_sequences = {
        '\\n': '\n',
        '\\r': '\r',
        '\\t': '\t',
        '\\"': '"',
        "\\'": "'",
        '\\\\': '\\',
        '\\b': '\b',
        '\\f': '\f',
        '\\v': '\v',
        '\\a': '\a',
    }
    
    # First handle literal backslash followed by escape character
    # This is a special case for strings like "\\n" which should be treated as "\n"
    result = text
    for escaped, unescaped in escape_sequences.items():
        # Only replace if the escape sequence is properly escaped
        # (i.e., not already part of a larger escape sequence)
        i = 0
        while i < len(result):
            if i + len(escaped) <= len(result) and result[i:i+len(escaped)] == escaped:
                # Check if this is part of a larger escape sequence
                if i > 0 and result[i-1] == '\\':
                    i += 1
                    continue
                # Replace the escape sequence
                result = result[:i] + unescaped + result[i+len(escaped):]
                i += 1
            else:
                i += 1
    
    return result

def is_escape_sequence_line(line: str) -> bool:
    """
    Check if a line contains escape sequences that need special handling.
    
    Args:
        line: The line to check
        
    Returns:
        True if the line contains escape sequences, False otherwise
    """
    # Check for common escape sequences
    for pattern in COMMON_ESCAPE_SEQUENCES:
        if re.search(pattern, line):
            return True
    
    # Check for escape sequences in various contexts
    for pattern in ESCAPE_SEQUENCE_PATTERNS:
        if re.search(pattern, line):
            return True
    
    return False

def handle_escape_sequence_line(line: str) -> str:
    """
    Handle a line with escape sequences, with special attention to trailing whitespace.
    
    Args:
        line: The line to handle
        
    Returns:
        The handled line
    """
    if not is_escape_sequence_line(line):
        return line
    
    # Process the line to handle escape sequences with trailing whitespace
    processed_line = line
    
    # Handle method calls with escape sequences and trailing whitespace
    # This pattern specifically targets .replace() method calls with escape sequences and trailing whitespace
    replace_pattern = r'(\.replace\([^)]*\\[rntbfv0\\\'\"](?:[^)]*)\)\s+)'
    processed_line = re.sub(replace_pattern, lambda m: m.group(1).rstrip(), processed_line)
    
    # Handle other method calls with trailing whitespace
    method_pattern = r'(\.\w+\([^)]*\\[rntbfv0\\\'\"](?:[^)]*)\)\s+)'
    processed_line = re.sub(method_pattern, lambda m: m.group(1).rstrip(), processed_line)
    
    # First, handle escape sequences in string literals
    def process_string_literals(match):
        quote = match.group(1)
        content = match.group(2)
        # Remove trailing whitespace after escape sequences in string content
        content = re.sub(r'(\\[rntbfv0\\\'\"]|\\/|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4})\s+', r'\1', content)
        return quote + content + quote
    
    # Process string literals
    processed_line = re.sub(r'(["\'])((?:\\.|[^\\"])*?)\\1', process_string_literals, processed_line)
    
    # Handle escape sequences in regex literals
    def process_regex_literals(match):
        regex_content = match.group(1)
        flags = match.group(2) or ''
        # Remove trailing whitespace after escape sequences in regex content
        regex_content = re.sub(r'(\\[rntbfv0\\\'\"]|\\/|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4})\s+', r'\1', regex_content)
        return regex_content + flags
    
    # Process regex literals
    processed_line = re.sub(r'(/[^/\\]*(?:\\.[^/\\]*)*/)([gim]*)', process_regex_literals, processed_line)
    
    # Handle escape sequences in template literals
    def process_template_literals(match):
        content = match.group(1)
        # Remove trailing whitespace after escape sequences in template literal content
        content = re.sub(r'(\\[rntbfv0\\\'\"]|\\/|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4})\s+', r'\1', content)
        return '`' + content + '`'
    
    # Process template literals
    processed_line = re.sub(r'`([^`\\]*(?:\\.[^`\\]*)*)`', process_template_literals, processed_line)
    
    # Handle method calls with escape sequences
    def process_method_calls(match):
        method_call = match.group(0)
        # Remove trailing whitespace after escape sequences in method call arguments
        return re.sub(r'(\\[rntbfv0\\\'\"]|\\/|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4})\s+', r'\1', method_call)
    
    # Process method calls
    for pattern in [r'\.replace\([^)]*\)', r'\.split\([^)]*\)', r'\.match\([^)]*\)']:
        processed_line = re.sub(pattern, process_method_calls, processed_line)
    
    # General case for any remaining escape sequences with trailing whitespace
    processed_line = re.sub(r'(\\[rntbfv0\\\'\"]|\\/|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4})\s+', r'\1', processed_line)
    
    return processed_line

def clean_escape_sequences_in_diff(diff_content: str) -> str:
    """
    Clean escape sequences in a diff, focusing on trailing whitespace.
    
    Args:
        diff_content: The diff content to clean
        
    Returns:
        The cleaned diff content
    """
    # Add debug logging
    logger.debug(f"Original diff content (first 200 chars): {repr(diff_content[:200])}")
    
    # First, handle specific patterns that might be missed by line-by-line processing
    # This is a generalized pattern for method calls with trailing whitespace
    diff_content = re.sub(
        r'(\.replace\([^)]*\\[rntbfv0\\\'\"](?:[^)]*)\))\s+',
        r'\1',
        diff_content
    )
    
    # Split the diff into lines
    lines = diff_content.splitlines(True)  # Keep line endings
    result_lines = []
    
    for line in lines:
        # Only process content lines (not headers or context)
        if line.startswith('+'):
            # For added lines, clean escape sequences
            content = line[1:]
            if is_escape_sequence_line(content):
                logger.debug(f"Processing line with escape sequences: {repr(content)}")
                cleaned_content = handle_escape_sequence_line(content)
                logger.debug(f"Cleaned content: {repr(cleaned_content)}")
                result_lines.append('+' + cleaned_content)
            else:
                result_lines.append(line)
        else:
            # Keep other lines as is
            result_lines.append(line)
    
    result = ''.join(result_lines)
    logger.debug(f"Cleaned diff content (first 200 chars): {repr(result[:200])}")
    return result

def apply_escape_sequence_fixes(lines: List[str]) -> List[str]:
    """
    Apply escape sequence fixes to a list of lines.
    
    Args:
        lines: The lines to fix
        
    Returns:
        The fixed lines
    """
    result = []
    for line in lines:
        if is_escape_sequence_line(line):
            result.append(handle_escape_sequence_line(line))
        else:
            result.append(line)
    return result

def find_escape_sequence_issues(original_content: str, modified_content: str) -> List[Tuple[int, str, str]]:
    """
    Find escape sequence issues between original and modified content.
    
    Args:
        original_content: The original content
        modified_content: The modified content
        
    Returns:
        A list of tuples (line_number, original_line, modified_line) where escape sequence issues were found
    """
    original_lines = original_content.splitlines()
    modified_lines = modified_content.splitlines()
    
    issues = []
    
    # Find lines with escape sequences that differ between original and modified
    for i, (orig_line, mod_line) in enumerate(zip(original_lines, modified_lines)):
        if orig_line != mod_line and (is_escape_sequence_line(orig_line) or is_escape_sequence_line(mod_line)):
            # Check if the difference is only in trailing whitespace after escape sequences
            cleaned_orig = handle_escape_sequence_line(orig_line)
            cleaned_mod = handle_escape_sequence_line(mod_line)
            
            if cleaned_orig == cleaned_mod:
                # The difference is only in trailing whitespace after escape sequences
                issues.append((i + 1, orig_line, mod_line))
    
    return issues
