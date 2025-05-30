"""
Utilities for handling diff hunks.
"""

from typing import List, Dict, Any, Tuple
import re
from app.utils.logging_utils import logger
from ..core.utils import calculate_block_similarity
from ..core.unicode_handling import normalize_unicode
from ..core.escape_handling import normalize_escape_sequences

def fix_hunk_context(lines: List[str]) -> List[str]:
    """
    Fix hunk headers to ensure proper context.
    
    Args:
        lines: The lines of the diff
        
    Returns:
        The lines with fixed hunk headers
    """
    result = []
    in_hunk = False
    hunk_lines = []
    
    for line in lines:
        if line.startswith('@@'):
            if in_hunk and hunk_lines:
                # Process previous hunk
                result.extend(fix_single_hunk_header(hunk_lines))
                hunk_lines = []
            in_hunk = True
            hunk_lines = [line]
        elif in_hunk:
            hunk_lines.append(line)
        else:
            result.append(line)
    
    # Process last hunk
    if in_hunk and hunk_lines:
        result.extend(fix_single_hunk_header(hunk_lines))
    
    return result

def fix_single_hunk_header(hunk_lines: List[str]) -> List[str]:
    """
    Fix a single hunk header based on actual content.
    
    Args:
        hunk_lines: The lines of the hunk
        
    Returns:
        The hunk lines with fixed header
    """
    if not hunk_lines:
        return []
        
    header = hunk_lines[0]
    content = hunk_lines[1:]
    
    # Count actual changes
    old_count = 0
    new_count = 0
    
    for line in content:
        if line.startswith(' '):
            old_count += 1
            new_count += 1
        elif line.startswith('-'):
            old_count += 1
        elif line.startswith('+'):
            new_count += 1
    
    # Extract original line numbers
    match = re.match(r'^@@ -(\d+),\d+ \+(\d+),\d+ @@', header)
    if match:
        old_start = int(match.group(1))
        new_start = int(match.group(2))
        # Create new header with correct counts
        new_header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@"
        return [new_header] + content
    
    return hunk_lines

def find_best_chunk_position(file_lines: List[str], chunk_lines: List[str], expected_pos: int) -> Tuple[int, float]:
    """Find the best position for a chunk based on content similarity."""
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Get search radius from environment or use default
    from ..core.config import get_search_radius
    search_radius = get_search_radius()
    
    # Look in a window around the expected position
    window_size = len(chunk_lines)
    search_start = max(0, expected_pos - search_radius)
    search_end = min(len(file_lines), expected_pos + search_radius)
    
    # Ensure we have enough lines to compare
    if search_end - search_start < window_size:
        search_end = min(len(file_lines), search_start + window_size + 5)
    
    # Log the search range for debugging
    logger.debug(f"Searching for best position between lines {search_start+1} and {search_end} (expected: {expected_pos+1})")
    
    for i in range(search_start, search_end - window_size + 1):
        window = file_lines[i:i + window_size]
        ratio = calculate_block_similarity(window, chunk_lines)
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = i
            logger.debug(f"Found better match at line {i+1} with ratio {ratio:.2f}")
    
    return best_pos, best_ratio

def normalize_line_for_comparison(line: str) -> str:
    """
    Normalize a line for comparison, handling whitespace, invisible characters, and escape sequences.
    
    Args:
        line: The line to normalize
        
    Returns:
        The normalized line
    """
    if not line:
        return ""
    
    # Remove invisible Unicode characters
    result = normalize_unicode(line)
    
    # Normalize escape sequences
    result = normalize_escape_sequences(result)
    
    # Normalize whitespace
    result = result.strip()
    
    return result

def fix_line_calculation(file_lines: List[str], hunk: Dict[str, Any], pos: int) -> List[str]:
    """
    Fix line calculation issues when applying a hunk.
    
    Args:
        file_lines: List of lines from the file
        hunk: Dictionary containing hunk information
        pos: Position to apply the hunk
        
    Returns:
        The modified lines
    """
    result = file_lines.copy()
    
    # Extract the old and new lines
    old_lines = []
    new_lines = []
    
    for line in hunk.get('old_block', []):
        if line.startswith(' '):
            old_lines.append(line[1:])
        elif line.startswith('-'):
            old_lines.append(line[1:])
    
    for line in hunk.get('old_block', []):
        if line.startswith(' '):
            new_lines.append(line[1:])
        elif line.startswith('+'):
            new_lines.append(line[1:])
    
    # Ensure pos is within bounds
    pos = max(0, min(pos, len(result)))
    
    # Calculate the end position with proper bounds checking
    end_pos = min(pos + len(old_lines), len(result))
    
    # Replace the old lines with the new ones
    result[pos:end_pos] = new_lines
    
    return result
