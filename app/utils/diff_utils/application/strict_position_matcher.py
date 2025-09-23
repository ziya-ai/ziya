"""
Strict position matcher for identical adjacent blocks.
"""

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

def find_strict_position_match(
    file_lines: List[str],
    old_lines: List[str],
    expected_pos: int,
    search_radius: int = 5
) -> Tuple[Optional[int], float]:
    """
    Find position using strict matching - look for exact line matches to avoid identical block confusion.
    """
    if not old_lines:
        return expected_pos, 1.0
    
    logger.debug(f"Strict position matching: expected_pos={expected_pos}, old_lines={len(old_lines)}")
    
    # Look for distinctive lines in the old_lines that we can use to find the exact position
    distinctive_lines = []
    for i, line in enumerate(old_lines):
        stripped = line.strip()
        if (stripped and 
            len(stripped) > 10 and 
            stripped not in ['return None', 'pass', '{}', '[]', 'True', 'False'] and
            not stripped.startswith('if value is None') and
            not stripped.startswith('if not isinstance(value, str)') and
            not stripped.startswith('if len(value) == 0')):
            distinctive_lines.append((i, stripped))
    
    if not distinctive_lines:
        # No distinctive lines, fall back to expected position
        logger.debug("No distinctive lines found, using expected position")
        return expected_pos, 0.7
    
    logger.debug(f"Found {len(distinctive_lines)} distinctive lines: {[line for _, line in distinctive_lines[:2]]}")
    
    # Find all positions where the first distinctive line appears
    first_distinctive_idx, first_distinctive_line = distinctive_lines[0]
    candidate_positions = []
    
    for file_line_idx, file_line in enumerate(file_lines):
        if file_line.strip() == first_distinctive_line:
            # Calculate where the hunk would start if this line matches
            hunk_start_pos = file_line_idx - first_distinctive_idx
            if hunk_start_pos >= 0 and hunk_start_pos + len(old_lines) <= len(file_lines):
                candidate_positions.append(hunk_start_pos)
    
    logger.debug(f"Found {len(candidate_positions)} candidate positions: {candidate_positions}")
    
    # Evaluate each candidate position
    best_pos = None
    best_score = 0.0
    
    for pos in candidate_positions:
        candidate_lines = file_lines[pos:pos + len(old_lines)]
        
        # Calculate how many lines match exactly
        exact_matches = sum(1 for old, cand in zip(old_lines, candidate_lines) 
                           if old.strip() == cand.strip())
        match_ratio = exact_matches / len(old_lines) if old_lines else 0.0
        
        # Prefer positions closer to expected
        distance = abs(pos - expected_pos)
        distance_penalty = min(distance / 10.0, 0.5)  # Cap penalty at 0.5
        adjusted_score = match_ratio * (1.0 - distance_penalty)
        
        logger.debug(f"Position {pos}: exact_matches={exact_matches}/{len(old_lines)}, "
                    f"match_ratio={match_ratio:.3f}, distance={distance}, "
                    f"adjusted_score={adjusted_score:.3f}")
        
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_pos = pos
    
    # Accept matches with reasonable confidence
    if best_pos is not None and best_score >= 0.5:
        logger.debug(f"Strict matching found position {best_pos} with score {best_score:.3f}")
        return best_pos, best_score
    
    # If no good match found, fall back to expected position
    logger.debug(f"No good strict match found (best score: {best_score:.3f}), using expected position")
    return expected_pos, 0.4

def lines_match_exactly(lines1: List[str], lines2: List[str]) -> bool:
    """Check if two lists of lines match exactly (ignoring leading/trailing whitespace)."""
    if len(lines1) != len(lines2):
        return False
    
    for l1, l2 in zip(lines1, lines2):
        if l1.strip() != l2.strip():
            return False
    
    return True

def should_use_strict_matching(file_lines: List[str], old_lines: List[str]) -> bool:
    """
    Determine if we should use strict matching based on whether there are 
    multiple similar patterns in the file.
    """
    if len(old_lines) < 3:
        return False
    
    # Look for the first distinctive line
    distinctive_line = None
    for line in old_lines:
        stripped = line.strip()
        if (stripped and 
            len(stripped) > 5 and 
            stripped not in ['return None', 'pass', '{}', '[]', 'True', 'False']):
            distinctive_line = stripped
            break
    
    if not distinctive_line:
        return False
    
    # Count occurrences of the distinctive line
    occurrences = sum(1 for file_line in file_lines 
                     if file_line.strip() == distinctive_line)
    
    if occurrences > 1:
        logger.debug(f"Found {occurrences} occurrences of distinctive line '{distinctive_line}', using strict matching")
        return True
    
    return False
