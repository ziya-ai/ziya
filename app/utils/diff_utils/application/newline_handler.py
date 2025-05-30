"""
Module for handling missing newline at end of file issues in diffs.
"""

import re
from typing import Optional, List, Tuple
from app.utils.logging_utils import logger

def has_missing_newline_marker(git_diff: str) -> bool:
    """
    Check if a diff contains the "No newline at end of file" marker.
    
    Args:
        git_diff: The git diff to check
        
    Returns:
        True if the diff contains the marker, False otherwise
    """
    return "\\ No newline at end of file" in git_diff

def detect_line_endings(content: str) -> Tuple[str, bool]:
    """
    Detect the dominant line ending in content and whether it has a final newline.
    
    Args:
        content: The content to analyze
        
    Returns:
        Tuple of (dominant_line_ending, has_final_newline)
    """
    crlf_count = content.count('\r\n')
    lf_count = content.count('\n') - crlf_count  # Subtract CRLF count to avoid double counting
    cr_count = content.count('\r') - crlf_count  # Subtract CRLF count to avoid double counting
    
    # Determine dominant line ending
    if crlf_count > 0 and crlf_count >= max(cr_count, lf_count):
        dominant_ending = '\r\n'
    elif cr_count > lf_count:
        dominant_ending = '\r'
    else:
        dominant_ending = '\n'
    
    # Check if content ends with a newline
    has_final_newline = bool(content) and content.endswith(('\n', '\r\n', '\r'))
    
    return dominant_ending, has_final_newline

def normalize_line_endings(content: str, target_ending: str = '\n') -> str:
    """
    Normalize all line endings in content to the target ending.
    
    Args:
        content: The content to normalize
        target_ending: The target line ending ('\n', '\r\n', or '\r')
        
    Returns:
        Content with normalized line endings
    """
    # First convert all line endings to '\n'
    normalized = content.replace('\r\n', '\n').replace('\r', '\n')
    
    # Then convert to target ending
    if target_ending != '\n':
        normalized = normalized.replace('\n', target_ending)
    
    return normalized

def fix_missing_newline_issue(original_content: str, git_diff: str) -> Optional[str]:
    """
    Fix issues with missing newline at end of file in a diff.
    
    Args:
        original_content: The original file content
        git_diff: The git diff to apply
        
    Returns:
        The modified content with newline issues fixed, or None if no changes were needed
    """
    logger.info("Processing missing newline at end of file issues")
    
    # Check if this diff has the missing newline marker
    if not has_missing_newline_marker(git_diff):
        logger.info("No missing newline markers found")
        return None
    
    # Parse the diff to determine if we need to add or remove a newline
    lines = git_diff.splitlines()
    add_newline = False
    remove_newline = False
    
    for i, line in enumerate(lines):
        if line == "\\ No newline at end of file":
            # Check if the previous line is a removal
            if i > 0 and lines[i-1].startswith('-'):
                # Original file was missing a newline, but new content has one
                add_newline = True
            # Check if the previous line is an addition
            elif i > 0 and lines[i-1].startswith('+'):
                # New content is missing a newline
                remove_newline = True
    
    # Apply the changes
    if add_newline and not original_content.endswith('\n'):
        logger.info("Adding missing newline at end of file")
        return original_content + '\n'
    elif remove_newline and original_content.endswith('\n'):
        logger.info("Removing newline at end of file")
        return original_content.rstrip('\n')
    
    # If we get here, no changes were needed
    logger.info("No newline changes needed")
    return None

def ensure_consistent_line_endings(content: str, dominant_ending: Optional[str] = None) -> str:
    """
    Ensure all line endings in content are consistent.
    
    Args:
        content: The content to normalize
        dominant_ending: The dominant line ending to use, or None to detect
        
    Returns:
        Content with consistent line endings
    """
    if dominant_ending is None:
        dominant_ending, _ = detect_line_endings(content)
    
    return normalize_line_endings(content, dominant_ending)

def fix_empty_file_newline(content: str) -> str:
    """
    Fix issues with empty files or files with only a newline.
    
    Args:
        content: The content to fix
        
    Returns:
        Fixed content
    """
    # If the file is empty or just a newline, return an empty string
    if not content or content == '\n' or content == '\r\n' or content == '\r':
        return ''
    
    # If the file starts with a newline but has other content, remove the leading newline
    if content.startswith(('\n', '\r\n', '\r')):
        if content.startswith('\r\n'):
            return content[2:]
        else:
            return content[1:]
    
    return content

def normalize_content_for_diff(content: str) -> List[str]:
    """
    Normalize content for difflib operations, preserving line endings.
    
    Args:
        content: The content to normalize
        
    Returns:
        List of lines with original line endings preserved
    """
    # Split content into lines, preserving line endings
    if not content:
        return []
    
    lines = []
    remaining = content
    
    while remaining:
        # Check for CRLF first (Windows)
        if remaining.startswith('\r\n'):
            lines.append('\r\n')
            remaining = remaining[2:]
        # Check for CR (old Mac)
        elif remaining.startswith('\r'):
            lines.append('\r')
            remaining = remaining[1:]
        # Check for LF (Unix)
        elif remaining.startswith('\n'):
            lines.append('\n')
            remaining = remaining[1:]
        # Find the next line ending
        else:
            crlf_pos = remaining.find('\r\n')
            cr_pos = remaining.find('\r')
            lf_pos = remaining.find('\n')
            
            # Find the earliest line ending
            positions = []
            if crlf_pos >= 0:
                positions.append((crlf_pos, '\r\n'))
            if cr_pos >= 0 and (cr_pos < crlf_pos or crlf_pos < 0) and (cr_pos + 1 != crlf_pos):
                positions.append((cr_pos, '\r'))
            if lf_pos >= 0:
                positions.append((lf_pos, '\n'))
            
            if positions:
                # Sort by position
                positions.sort()
                pos, ending = positions[0]
                lines.append(remaining[:pos] + ending)
                remaining = remaining[pos + len(ending):]
            else:
                # No more line endings
                lines.append(remaining)
                break
    
    return lines
