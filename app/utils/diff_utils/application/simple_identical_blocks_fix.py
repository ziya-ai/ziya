"""
Simple fix for identical adjacent blocks by improving position selection.
"""

import logging
import difflib
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

def find_best_position_for_identical_blocks(
    file_lines: List[str],
    old_lines: List[str], 
    expected_pos: int,
    search_radius: int = 10
) -> Tuple[Optional[int], float]:
    """
    Find the best position for applying changes when there are identical blocks.
    
    This function is more conservative and prefers positions closer to the expected position.
    """
    if not old_lines:
        return expected_pos, 1.0
    
    # First, check if the expected position is an exact match
    if (expected_pos + len(old_lines) <= len(file_lines) and
        expected_pos >= 0):
        
        candidate_lines = file_lines[expected_pos:expected_pos + len(old_lines)]
        if lines_match_exactly(old_lines, candidate_lines):
            logger.debug(f"Exact match found at expected position {expected_pos}")
            return expected_pos, 1.0
    
    # Search in a small radius around the expected position
    best_pos = None
    best_score = 0.0
    
    start_search = max(0, expected_pos - search_radius)
    end_search = min(len(file_lines) - len(old_lines), expected_pos + search_radius)
    
    for pos in range(start_search, end_search + 1):
        if pos + len(old_lines) > len(file_lines):
            continue
            
        candidate_lines = file_lines[pos:pos + len(old_lines)]
        
        # Calculate match score
        match_score = calculate_match_score(old_lines, candidate_lines)
        
        # Add distance penalty - prefer positions closer to expected
        distance_penalty = abs(pos - expected_pos) / max(search_radius, 1)
        adjusted_score = match_score * (1.0 - distance_penalty * 0.3)
        
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_pos = pos
            
        logger.debug(f"Position {pos}: match_score={match_score:.3f}, "
                    f"distance_penalty={distance_penalty:.3f}, "
                    f"adjusted_score={adjusted_score:.3f}")
    
    return best_pos, best_score

def lines_match_exactly(lines1: List[str], lines2: List[str]) -> bool:
    """Check if two lists of lines match exactly (ignoring whitespace)."""
    if len(lines1) != len(lines2):
        return False
    
    for l1, l2 in zip(lines1, lines2):
        if l1.strip() != l2.strip():
            return False
    
    return True

def calculate_match_score(lines1: List[str], lines2: List[str]) -> float:
    """Calculate how well two lists of lines match."""
    if not lines1 and not lines2:
        return 1.0
    if not lines1 or not lines2:
        return 0.0
    
    # Use difflib for similarity
    text1 = '\n'.join(line.strip() for line in lines1)
    text2 = '\n'.join(line.strip() for line in lines2)
    
    return difflib.SequenceMatcher(None, text1, text2).ratio()

def detect_and_fix_identical_blocks_issue(
    file_lines: List[str],
    old_lines: List[str],
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Detect if this is an identical blocks case and return a better position.
    """
    # Look for other occurrences of similar patterns
    similar_positions = []
    
    if len(old_lines) < 3:
        # Too short to be meaningful
        return None, 0.0
    
    # Find the most distinctive line in the pattern
    distinctive_line = None
    for line in old_lines:
        stripped = line.strip()
        if (stripped and 
            len(stripped) > 10 and 
            stripped not in ['return None', 'pass', '{}', '[]'] and
            not stripped.startswith('#')):
            distinctive_line = stripped
            break
    
    if not distinctive_line:
        return None, 0.0
    
    # Find all occurrences of the distinctive line
    for i, file_line in enumerate(file_lines):
        if file_line.strip() == distinctive_line:
            similar_positions.append(i)
    
    if len(similar_positions) <= 1:
        # Not an identical blocks case
        return None, 0.0
    
    logger.debug(f"Found identical blocks case with {len(similar_positions)} similar positions: {similar_positions}")
    
    # Use the improved position finding
    return find_best_position_for_identical_blocks(file_lines, old_lines, expected_pos)
