"""
Escape sequence handling utilities for diff application.
Uses shared escape_utils for core functionality.
"""

from .escape_utils import (
    normalize_escape_sequences as _normalize_escape_sequences,
    contains_escape_sequences,
    handle_escape_sequences_in_hunk
)


def normalize_escape_sequences(text: str, preserve_literals: bool = True) -> str:
    """
    Normalize escape sequences in text.
    
    Args:
        text: The text to normalize
        preserve_literals: If True, preserve escape sequences as literals (e.g., '\\n' stays as '\\n')
                          If False, convert escape sequences to their actual characters (e.g., '\\n' becomes a newline)
        
    Returns:
        The normalized text
    """
    if not text:
        return ""
    
    if preserve_literals:
        # When preserving literals, we don't convert escape sequences
        # This is important for code comparison where we want to compare the literal text
        return text
    
    # Use shared utility for normalization
    return _normalize_escape_sequences(text)


def handle_escape_sequences(original_content: str, git_diff: str) -> str:
    """
    Handle escape sequences in a diff.
    This is a general-purpose handler that works for any file with escape sequences.
    
    Args:
        original_content: The original content
        git_diff: The git diff to apply
        
    Returns:
        The modified content with escape sequences handled properly
    """
    # Check if this diff contains escape sequences that need special handling
    if '\\n' not in git_diff and '\\r' not in git_diff and '\\t' not in git_diff and '\\\\' not in git_diff:
        return None
    
    import re
    
    # Split the content and diff into lines
    original_lines = original_content.splitlines(True)
    diff_lines = git_diff.splitlines()
    
    # Process the diff to extract changes
    result_lines = original_lines.copy()
    current_line = None
    in_hunk = False
    
    for line in diff_lines:
        if line.startswith('@@'):
            # Extract line number from hunk header
            match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if match:
                current_line = int(match.group(1)) - 1  # Convert to 0-based
                in_hunk = True
        elif in_hunk:
            if line.startswith('-'):
                # This is a removal line
                if current_line is not None and current_line < len(result_lines):
                    # Don't remove the line yet, just increment the counter
                    current_line += 1
            elif line.startswith('+'):
                # This is an addition line
                content = line[1:]
                
                # Check if this line contains escape sequences
                if '\\n' in content or '\\r' in content or '\\t' in content or '\\\\' in content:
                    # Handle escape sequences
                    normalized_content = normalize_escape_sequences(content)
                    
                    # Special handling for text += lines
                    if '+=' in content:
                        # This is a text append operation
                        # Check if we need to add this line or if it's already in the file
                        append_match = re.match(r'^(\s*)([a-zA-Z0-9_]+)\s*\+=\s*(.+)$', content)
                        if append_match:
                            indentation, var_name, value = append_match.groups()
                            normalized_value = normalize_escape_sequences(value)
                            
                            # Check if this append operation is already in the file
                            found = False
                            for i, file_line in enumerate(result_lines):
                                if f"{var_name} +=" in file_line:
                                    file_append_match = re.match(r'^(\s*)([a-zA-Z0-9_]+)\s*\+=\s*(.+)$', file_line)
                                    if file_append_match and file_append_match.group(2) == var_name:
                                        file_value = file_append_match.group(3)
                                        normalized_file_value = normalize_escape_sequences(file_value)
                                        if normalized_file_value.strip() == normalized_value.strip():
                                            found = True
                                            break
                            
                            if not found:
                                # Add the line with normalized escape sequences
                                if current_line is not None:
                                    if current_line < len(result_lines):
                                        result_lines.insert(current_line, normalized_content)
                                    else:
                                        result_lines.append(normalized_content)
                        else:
                            # Not a text append operation, just add the line
                            if current_line is not None:
                                if current_line < len(result_lines):
                                    result_lines.insert(current_line, normalized_content)
                                else:
                                    result_lines.append(normalized_content)
                    else:
                        # Not a text append operation, just add the line
                        if current_line is not None:
                            if current_line < len(result_lines):
                                result_lines.insert(current_line, normalized_content)
                            else:
                                result_lines.append(normalized_content)
                else:
                    # No escape sequences, just add the line normally
                    if current_line is not None:
                        if current_line < len(result_lines):
                            result_lines.insert(current_line, content)
                        else:
                            result_lines.append(content)
            elif line.startswith(' '):
                # This is a context line
                if current_line is not None:
                    current_line += 1
    
    return ''.join(result_lines)

def detect_escape_sequence_pattern(text: str) -> bool:
    """
    Detect if text contains a pattern of escape sequences that needs special handling.
    
    Args:
        text: The text to check
        
    Returns:
        True if the text contains a pattern of escape sequences, False otherwise
    """
    # Check for common patterns
    patterns = [
        r'\\n',  # Newline escape
        r'\\r',  # Carriage return escape
        r'\\t',  # Tab escape
        r'\\"',  # Double quote escape
        r"\\'",  # Single quote escape
        r'\\\\', # Backslash escape
    ]
    
    import re
    for pattern in patterns:
        if re.search(pattern, text):
            return True
    
    return False

def handle_text_append_with_escapes(file_lines: list[str], diff_content: str) -> list[str]:
    """
    Handle text append operations with escape sequences.
    
    Args:
        file_lines: The lines of the file
        diff_content: The diff content
        
    Returns:
        The modified file lines
    """
    import re
    
    # Check if this is a text append operation
    if 'text +=' not in diff_content:
        return file_lines
    
    # Split the diff into lines
    diff_lines = diff_content.splitlines()
    
    # Extract all text append operations
    append_operations = []
    for line in diff_lines:
        if line.startswith('+') and '+=' in line and not line.startswith('+++'):
            content = line[1:]
            append_match = re.match(r'^(\s*)([a-zA-Z0-9_]+)\s*\+=\s*(.+)$', content)
            if append_match:
                indentation, var_name, value = append_match.groups()
                append_operations.append((indentation, var_name, value))
    
    # If no append operations found, return the original lines
    if not append_operations:
        return file_lines
    
    # Process each append operation
    result = file_lines.copy()
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
    
    return result
