"""
Module for applying hunks with improved handling of special cases.
"""

import re
import logging
from typing import List, Dict, Any, Tuple, Optional

# Configure logging
logger = logging.getLogger(__name__)

def apply_hunk_with_deduplication(file_lines: List[str], hunk: Dict[str, Any], position: int) -> List[str]:
    """
    Apply a hunk to file lines with deduplication detection to prevent duplicate code.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        position: The position to apply the hunk
        
    Returns:
        The modified file lines
    """
    # Extract the old and new blocks
    old_block = hunk.get('old_block', [])
    new_block = hunk.get('new_block', [])
    
    # Extract the lines to remove and add
    old_lines = [line[1:] for line in old_block if line.startswith('-')]
    new_lines = [line[1:] for line in new_block if line.startswith('+')]
    
    # Check if the new lines are already present in the file
    # This helps prevent duplicate code when applying hunks
    result = file_lines.copy()
    
    # Check for duplicates in the surrounding context
    context_size = 5  # Look 5 lines before and after
    start_check = max(0, position - context_size)
    end_check = min(len(result), position + context_size)
    
    # Extract the context to check
    context_to_check = result[start_check:end_check]
    
    # Check if all new lines are already present in the right order
    for i in range(len(context_to_check) - len(new_lines) + 1):
        if all(context_to_check[i+j].rstrip() == new_lines[j].rstrip() for j in range(len(new_lines))):
            logger.info(f"Detected duplicate code at position {start_check + i}, skipping hunk application")
            return result
    
    # Apply the hunk normally
    # Remove old lines
    for i, line in enumerate(old_block):
        if line.startswith('-'):
            if position + i < len(result):
                result.pop(position + i)
    
    # Add new lines
    for i, line in enumerate(new_block):
        if line.startswith('+'):
            result.insert(position + i, line[1:])
    
    return result

def apply_hunk_with_context_matching(file_lines: List[str], hunk: Dict[str, Any]) -> Tuple[List[str], bool]:
    """
    Apply a hunk using context matching to find the best position.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        
    Returns:
        Tuple of (modified_lines, success)
    """
    # Extract context lines from the hunk
    context_before = []
    context_after = []
    
    old_block = hunk.get('old_block', [])
    
    # Find context lines before and after the changes
    in_before = True
    for line in old_block:
        if line.startswith('-'):
            in_before = False
        elif line.startswith(' '):
            if in_before:
                context_before.append(line[1:])
            else:
                context_after.append(line[1:])
    
    # Find the best position to apply the hunk
    best_pos = None
    best_score = 0
    
    for i in range(len(file_lines) - len(context_before) - len(context_after) + 1):
        # Check context before
        before_score = 0
        for j, ctx_line in enumerate(context_before):
            if i + j < len(file_lines) and file_lines[i + j].rstrip() == ctx_line.rstrip():
                before_score += 1
        
        # Check context after
        after_pos = i + len(context_before) + len([l for l in old_block if l.startswith('-')])
        after_score = 0
        for j, ctx_line in enumerate(context_after):
            if after_pos + j < len(file_lines) and file_lines[after_pos + j].rstrip() == ctx_line.rstrip():
                after_score += 1
        
        # Calculate total score
        total_score = before_score + after_score
        if total_score > best_score:
            best_score = total_score
            best_pos = i
    
    # If we found a good position, apply the hunk
    if best_pos is not None and best_score > 0:
        return apply_hunk_with_deduplication(file_lines, hunk, best_pos), True
    
    return file_lines, False

def handle_duplicate_code_detection(file_lines: List[str], hunks: List[Dict[str, Any]]) -> List[str]:
    """
    Apply hunks with duplicate code detection to prevent code duplication.
    
    Args:
        file_lines: The lines of the file
        hunks: The hunks to apply
        
    Returns:
        The modified file lines
    """
    result = file_lines.copy()
    
    for hunk in hunks:
        # Try to apply the hunk with context matching
        new_result, success = apply_hunk_with_context_matching(result, hunk)
        if success:
            result = new_result
    
    return result
