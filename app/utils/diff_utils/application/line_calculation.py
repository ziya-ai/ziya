"""
Improved line calculation utilities for diff application.
"""

from typing import List, Dict, Any, Tuple, Optional
import difflib
from app.utils.logging_utils import logger
from ..core.text_normalization import normalize_text_for_comparison
from ..core.config import (
    get_confidence_threshold,
    get_search_radius,
    get_context_size,
    calculate_adaptive_context_size,
    EXACT_MATCH_THRESHOLD,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD
)

def calculate_line_positions(file_lines: List[str], hunk: Dict[str, Any], line_offset: int) -> Tuple[int, int]:
    """
    Calculate the correct line positions for applying a hunk with proper bounds checking.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        line_offset: The current line offset from previous hunks
        
    Returns:
        Tuple of (start_position, end_position)
    """
    old_start = max(0, hunk['old_start'] - 1)  # Convert to 0-based indexing, ensure non-negative
    old_count = hunk['old_count']
    
    # Adjust for previous hunks
    adjusted_start = max(0, old_start + line_offset)
    
    # Ensure we don't go out of bounds
    adjusted_start = max(0, min(adjusted_start, len(file_lines)))
    
    # Calculate available lines based on the current state of the file
    available_lines = len(file_lines) - adjusted_start
    
    # Adjust old_count if we're near the end of file
    actual_old_count = min(old_count, available_lines)
    
    # Calculate the end position for removal with proper bounds checking
    end_position = min(adjusted_start + actual_old_count, len(file_lines))
    
    # Log detailed information about the calculation
    logger.debug(f"Hunk: Raw old_start={old_start}")
    logger.debug(f"Hunk: Calculated positions - old_start(0based)={old_start}, old_count={old_count}, offset={line_offset}")
    logger.debug(f"Hunk: initial_remove_pos={adjusted_start}, available_lines={available_lines}, actual_old_count={actual_old_count}")
    logger.debug(f"Hunk: final remove_pos={adjusted_start}, end_remove={end_position}")

    return adjusted_start, end_position

def find_best_position(file_lines: List[str], hunk: Dict[str, Any], expected_pos: int) -> Optional[Tuple[int, float]]:
    """
    Find the best position for a hunk based on context.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        expected_pos: The expected position
        
    Returns:
        Tuple of (position, similarity_ratio) or None if no good match found
    """
    # Extract context lines from the hunk
    context_lines = []
    for line in hunk.get('old_block', []):
        if line.startswith(' '):
            context_lines.append(line[1:])
    
    if not context_lines:
        logger.debug(f"No context lines found in hunk, cannot find best position")
        return None
    
    # Convert to a single block for comparison
    context_block = '\n'.join(context_lines)
    
    # Normalize the file lines and context for comparison
    normalized_file = [normalize_text_for_comparison(line) for line in file_lines]
    normalized_context = [normalize_text_for_comparison(line) for line in context_lines]
    
    # First try exact matching at the expected position
    if expected_pos >= 0 and expected_pos + len(context_lines) <= len(file_lines):
        exact_match = True
        for i in range(len(context_lines)):
            if normalized_file[expected_pos + i] != normalized_context[i]:
                exact_match = False
                break
        
        if exact_match:
            logger.debug(f"Found exact match at expected position {expected_pos}")
            return (expected_pos, EXACT_MATCH_THRESHOLD)
    
    # If exact match fails, try fuzzy matching around the expected position
    # Use a configurable search radius for better results
    search_radius = get_search_radius()
    search_start = max(0, expected_pos - search_radius)
    search_end = min(len(file_lines), expected_pos + search_radius)
    
    best_match = expected_pos
    best_ratio = 0.0
    
    # Calculate an adaptive window size based on the hunk size
    hunk_size = len(context_lines)
    window_sizes = [hunk_size]
    
    # For larger hunks, also try with smaller windows for more robust matching
    if hunk_size > 3:
        # Add smaller window sizes for better matching
        window_sizes.append(3)
        if hunk_size > 10:
            window_sizes.append(5)
    
    for window_size in window_sizes:
        if window_size > len(context_lines):
            continue
            
        # For smaller windows, use the beginning of the context
        context_to_match = normalized_context[:window_size]
        context_str = '\n'.join(context_to_match)
        
        for i in range(search_start, search_end - window_size + 1):
            window = normalized_file[i:i + window_size]
            window_str = '\n'.join(window)
            
            # Use SequenceMatcher for better fuzzy matching
            ratio = difflib.SequenceMatcher(None, window_str, context_str).ratio()
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = i
                logger.debug(f"Found better match at position {i} with ratio {ratio:.2f}")
    
    # If we found a good match, return it
    medium_threshold = get_confidence_threshold('medium')
    if best_ratio > medium_threshold:
        logger.debug(f"Best position found at {best_match} with ratio {best_ratio:.2f}")
        return (best_match, best_ratio)
    
    # If no good match found, log the issue
    logger.error(f"No good match found for hunk. Best ratio was {best_ratio:.2f} at position {best_match}")
    
    # As a last resort, return the expected position with a low confidence
    return (expected_pos, best_ratio)
def verify_position_with_content(file_lines: List[str], hunk: Dict[str, Any], position: int) -> Tuple[bool, float]:
    """
    Verify if a position is valid for applying a hunk by comparing content.
    
    Args:
        file_lines: The lines of the file
        hunk: The hunk to apply
        position: The position to verify
        
    Returns:
        Tuple of (is_valid, confidence)
    """
    if position < 0 or position >= len(file_lines):
        return False, 0.0
    
    # Extract the lines that should be removed according to the hunk
    old_lines = []
    for line in hunk.get('old_block', []):
        if line.startswith(' ') or line.startswith('-'):
            old_lines.append(line[1:])
    
    if not old_lines:
        return True, EXACT_MATCH_THRESHOLD  # No lines to remove, position is valid
    
    # Check if we have enough lines in the file
    if position + len(old_lines) > len(file_lines):
        return False, 0.0
    
    # Compare the lines
    matching_lines = 0
    for i, old_line in enumerate(old_lines):
        file_line = file_lines[position + i]
        
        # Normalize both lines for comparison
        norm_old = normalize_text_for_comparison(old_line)
        norm_file = normalize_text_for_comparison(file_line)
        
        if norm_old == norm_file:
            matching_lines += 1
    
    # Calculate confidence
    confidence = matching_lines / len(old_lines) if old_lines else EXACT_MATCH_THRESHOLD
    medium_threshold = get_confidence_threshold('medium')
    
    return confidence > medium_threshold, confidence
