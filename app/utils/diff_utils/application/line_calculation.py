"""
Improved line calculation utilities for diff application.
"""

from typing import List, Dict, Any, Tuple, Optional

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
    old_start = hunk['old_start'] - 1  # Convert to 0-based indexing
    old_count = hunk['old_count']
    
    # Adjust for previous hunks
    adjusted_start = old_start + line_offset
    
    # Ensure we don't go out of bounds
    adjusted_start = max(0, min(adjusted_start, len(file_lines)))
    
    # Calculate available lines based on the current state of the file
    available_lines = len(file_lines) - adjusted_start
    
    # Adjust old_count if we're near the end of file
    actual_old_count = min(old_count, available_lines)
    
    # Calculate the end position for removal with proper bounds checking
    end_position = min(adjusted_start + actual_old_count, len(file_lines))
    
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
    from ..core.text_normalization import normalize_line, similarity_ratio
    
    # Extract context lines from the hunk
    context_lines = []
    for line in hunk['old_block']:
        if line.startswith(' '):
            context_lines.append(line[1:])
    
    if not context_lines:
        return None
    
    # Convert to a single block for comparison
    context_block = '\n'.join(context_lines)
    
    # Normalize the file lines for comparison
    normalized_file = [normalize_line(line) for line in file_lines]
    normalized_context = [normalize_line(line) for line in context_lines]
    
    # Search around the expected position
    search_start = max(0, expected_pos - 50)
    search_end = min(len(file_lines), expected_pos + 50)
    
    best_match = expected_pos
    best_ratio = 0.0
    
    for i in range(search_start, search_end):
        if i + len(context_lines) <= len(file_lines):
            window = '\n'.join(normalized_file[i:i + len(context_lines)])
            ratio = similarity_ratio(window, '\n'.join(normalized_context))
            
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = i
    
    # If we found a good match, return it
    if best_ratio > 0.8:
        return (best_match, best_ratio)
    
    return None
