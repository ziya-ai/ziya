"""
Module for handling missing newline at end of file issues in diffs.
"""

import re
from typing import Optional, List
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

def process_newline_changes(original_content: str, git_diff: str, modified_content: Optional[str] = None) -> str:
    """
    Process newline changes in a diff, ensuring the file ends with a newline if needed.
    
    Args:
        original_content: The original file content
        git_diff: The git diff to apply
        modified_content: Optional modified content to use as a base
        
    Returns:
        The content with proper newline handling
    """
    # Start with either the modified content or the original
    content = modified_content if modified_content is not None else original_content
    
    # Check if the diff adds content to the end of the file
    adds_content_at_end = False
    lines = git_diff.splitlines()
    
    # Extract hunks from the diff
    hunks = []
    current_hunk = []
    in_hunk = False
    
    for line in lines:
        if line.startswith('@@'):
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = [line]
            in_hunk = True
        elif in_hunk:
            current_hunk.append(line)
    
    if current_hunk:
        hunks.append(current_hunk)
    
    # Check the last hunk to see if it adds content at the end
    if hunks:
        last_hunk = hunks[-1]
        hunk_header = last_hunk[0]
        
        # Parse the hunk header to get line numbers
        # Format: @@ -old_start,old_count +new_start,new_count @@
        match = re.match(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', hunk_header)
        if match:
            old_start = int(match.group(1))
            old_count = int(match.group(2))
            
            # Calculate if this hunk affects the end of the file
            original_lines = original_content.splitlines()
            if old_start + old_count - 1 >= len(original_lines):
                # This hunk affects the end of the file
                # Check if it adds content
                for line in last_hunk[1:]:
                    if line.startswith('+') and not line.startswith('+++'):
                        adds_content_at_end = True
                        break
    
    # If the diff adds content at the end, ensure it ends with a newline
    if adds_content_at_end and not content.endswith('\n'):
        logger.info("Adding newline at end of file for added content")
        content += '\n'
    
    # Check for explicit newline markers and handle them
    newline_fixed_content = fix_missing_newline_issue(content, git_diff)
    if newline_fixed_content is not None:
        content = newline_fixed_content
    
    return content
