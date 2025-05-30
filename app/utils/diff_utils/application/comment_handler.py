"""
Utilities for handling comment-only changes in diffs.
"""

import re
from typing import List, Tuple, Optional, Dict, Set

from app.utils.logging_utils import logger

def is_comment_line(line: str) -> bool:
    """
    Check if a line is a comment line.
    
    Args:
        line: The line to check
        
    Returns:
        True if the line is a comment line
    """
    # Python comments
    if line.strip().startswith('#'):
        return True
    
    # Python docstrings
    if line.strip().startswith('"""') or line.strip().startswith("'''"):
        return True
    
    # Python docstring content (indented)
    if line.strip() and not line.strip()[0].isalnum() and not line.strip()[0] in '([{':
        return True
    
    # TODO/FIXME comments
    if 'TODO:' in line or 'FIXME:' in line:
        return True
    
    return False

def extract_comment_changes(diff_content: str) -> List[Tuple[int, str, str]]:
    """
    Extract comment changes from a diff.
    
    Args:
        diff_content: The diff content to analyze
        
    Returns:
        List of tuples (line_number, old_comment, new_comment)
    """
    comment_changes = []
    current_line = 0
    in_hunk = False
    old_comments = {}
    new_comments = {}
    
    lines = diff_content.splitlines()
    
    for i, line in enumerate(lines):
        # Track hunk headers to get line numbers
        if line.startswith('@@'):
            match = re.search(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if match:
                old_start = int(match.group(1))
                old_count = int(match.group(2))
                new_start = int(match.group(3))
                new_count = int(match.group(4))
                current_line = old_start
                in_hunk = True
                continue
        
        if not in_hunk:
            continue
            
        # Track removed comment lines
        if line.startswith('-') and is_comment_line(line[1:]):
            old_comments[current_line] = line[1:]
            current_line += 1
        # Track added comment lines
        elif line.startswith('+') and is_comment_line(line[1:]):
            new_comments[current_line] = line[1:]
        # Track context lines
        elif line.startswith(' '):
            current_line += 1
        # Skip other lines
        else:
            if not line.startswith('-') and not line.startswith('+'):
                current_line += 1
    
    # Match old and new comments
    for line_num in sorted(set(old_comments.keys()) | set(new_comments.keys())):
        old_comment = old_comments.get(line_num, '')
        new_comment = new_comments.get(line_num, '')
        if old_comment or new_comment:
            comment_changes.append((line_num, old_comment, new_comment))
    
    return comment_changes

def is_comment_only_diff(diff_content: str) -> bool:
    """
    Check if a diff contains only comment changes.
    
    Args:
        diff_content: The diff content to check
        
    Returns:
        True if the diff only contains comment changes
    """
    non_comment_changes = False
    in_hunk = False
    
    lines = diff_content.splitlines()
    
    for line in lines:
        # Skip hunk headers and file headers
        if line.startswith('@@') or line.startswith('diff') or line.startswith('index') or line.startswith('---') or line.startswith('+++'):
            if line.startswith('@@'):
                in_hunk = True
            continue
        
        if not in_hunk:
            continue
            
        # Check if there are non-comment changes
        if line.startswith('-') and not is_comment_line(line[1:]):
            non_comment_changes = True
            break
        if line.startswith('+') and not is_comment_line(line[1:]):
            non_comment_changes = True
            break
    
    return not non_comment_changes

def apply_comment_changes(file_path: str, diff_content: str) -> Optional[bool]:
    """
    Apply comment changes from a diff to a file.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        
    Returns:
        True if the changes were applied, None otherwise
    """
    if not is_comment_only_diff(diff_content):
        return None
    
    logger.info(f"Handling comment-only changes for {file_path}")
    
    # Read the original file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except FileNotFoundError:
        original_content = ""
    
    # Parse the diff to get expected content
    import difflib
    from ..parsing.diff_parser import parse_unified_diff_exact_plus
    
    # Create a copy of the original content to modify
    original_lines = original_content.splitlines()
    expected_lines = original_lines.copy()
    
    # Apply each hunk to get the expected content
    hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
    line_offset = 0  # Track the offset caused by adding/removing lines
    
    for hunk_idx, h in enumerate(hunks, start=1):
        old_start = h['old_start'] - 1  # Convert to 0-based indexing
        old_count = len(h['old_block'])
        
        # Extract context and changes
        removed_lines = h['old_block']
        added_lines = h['new_lines']
        
        # Find the position to apply the changes
        expected_pos = old_start + line_offset
        
        # Calculate the end position for removal
        end_position = min(expected_pos + old_count, len(expected_lines))
        
        # Apply the changes
        expected_lines = expected_lines[:expected_pos] + added_lines + expected_lines[end_position:]
        
        # Update the line offset
        line_offset += len(added_lines) - old_count
    
    # Write the result back to the file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(expected_lines))
        # Ensure the file ends with a newline
        if expected_lines:
            f.write('\n')
    
    logger.info(f"Successfully applied comment changes to {file_path}")
    return True
