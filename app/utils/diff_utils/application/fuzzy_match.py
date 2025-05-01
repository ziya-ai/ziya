"""
Fuzzy matching utilities for diff application.
"""

import difflib
import logging
from typing import List, Optional, Tuple
import os
from ..core.config import get_search_radius, get_confidence_threshold

def find_best_chunk_position(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position in file_lines to apply chunk_lines.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
        If no good match is found, position will be None
    """
    if not chunk_lines:
        return expected_pos, 1.0  # Empty chunks match perfectly at the expected position

    # Get search radius from config
    search_radius = get_search_radius()
    
    # Get confidence threshold from config (using 'medium' level)
    confidence_threshold = get_confidence_threshold('medium')
    
    # Calculate search range
    start_pos = max(0, expected_pos - search_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + search_radius) if expected_pos is not None else len(file_lines)
    
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Join chunk lines for comparison
    chunk_text = ''.join(chunk_lines)
    
    # Search for the best match within the search radius
    for pos in range(start_pos, end_pos):
        # Make sure we don't go out of bounds
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        # Get the slice of file_lines to compare
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        file_text = ''.join(file_slice)
        
        # Calculate similarity ratio
        ratio = difflib.SequenceMatcher(None, chunk_text, file_text).ratio()
        
        # Update best match if this is better
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
    
    # Return None if confidence is too low
    if best_ratio < confidence_threshold and (best_pos != expected_pos or expected_pos is None):
        return None, best_ratio
    
    return best_pos, best_ratio
