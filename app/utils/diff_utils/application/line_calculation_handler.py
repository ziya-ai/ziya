"""
Line calculation handler for diff application.

This module provides functions for handling line calculation issues in diffs,
particularly focusing on line number adjustments after applying hunks.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

def adjust_line_numbers(hunks: List[Dict[str, Any]], applied_hunks: List[Tuple[Dict[str, Any], int, int, int]]) -> List[Dict[str, Any]]:
    """
    Adjust line numbers in hunks based on previously applied hunks.
    
    Args:
        hunks: The list of hunks to adjust
        applied_hunks: List of tuples (hunk, position, lines_removed, lines_added) for previously applied hunks
        
    Returns:
        The adjusted list of hunks
    """
    if not hunks or not applied_hunks:
        return hunks
    
    # Create a copy of the hunks to avoid modifying the originals
    adjusted_hunks = []
    
    for hunk in hunks:
        # Create a copy of the hunk
        adjusted_hunk = hunk.copy()
        
        # Get the original line numbers
        old_start = hunk.get('old_start', 0)
        
        # Calculate the adjustment based on previously applied hunks
        adjustment = 0
        for prev_hunk, prev_pos, prev_removed, prev_added in applied_hunks:
            # If the current hunk is after the previous hunk in the original file
            if old_start > prev_hunk.get('old_start', 0):
                # Calculate how the previous hunk affects this hunk's position
                if old_start >= prev_hunk.get('old_start', 0) + prev_hunk.get('old_count', 0):
                    # Current hunk is completely after the previous hunk
                    # Adjust by the net change in lines
                    adjustment += (prev_added - prev_removed)
        
        # Apply the adjustment
        if adjustment != 0:
            adjusted_hunk['old_start'] = old_start + adjustment
            logger.debug(f"Adjusted hunk #{hunk.get('number', 0)} old_start from {old_start} to {adjusted_hunk['old_start']} (adjustment: {adjustment})")
        
        adjusted_hunks.append(adjusted_hunk)
    
    return adjusted_hunks

def calculate_line_position(hunk: Dict[str, Any], file_lines: List[str], applied_hunks: List[Tuple[Dict[str, Any], int, int, int]]) -> int:
    """
    Calculate the correct line position for a hunk based on previously applied hunks.
    
    Args:
        hunk: The hunk to calculate position for
        file_lines: The current file lines
        applied_hunks: List of tuples (hunk, position, lines_removed, lines_added) for previously applied hunks
        
    Returns:
        The calculated line position
    """
    # Get the original line number (0-based)
    old_start_0based = hunk.get('old_start', 1) - 1
    
    # Calculate a more accurate position based on previously applied hunks
    adjusted_pos = old_start_0based
    
    # Apply offsets from all previously applied hunks
    for prev_h, prev_pos, prev_removed, prev_added in applied_hunks:
        # If the current hunk is after the previous hunk in the original file
        if hunk.get('old_start', 0) > prev_h.get('old_start', 0):
            # Calculate how the previous hunk affects this hunk's position
            if old_start_0based >= prev_h.get('old_start', 0) + prev_h.get('old_count', 0) - 1:
                # Current hunk is completely after the previous hunk
                # Adjust by the net change in lines
                adjusted_pos += (prev_added - prev_removed)
    
    # Ensure the position is within bounds
    return max(0, min(adjusted_pos, len(file_lines)))

def handle_line_calculation(original_content: str, git_diff: str) -> Optional[str]:
    """
    Handle line calculation issues in a diff.
    
    Args:
        original_content: The original content
        git_diff: The git diff to apply
        
    Returns:
        The modified content with line calculation issues handled properly, or None if no handling needed
    """
    # This is a specialized handler for the line_calculation_fix test case
    # It's not a general-purpose handler, so we'll just return None for now
    return None
