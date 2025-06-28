"""
Duplication preventer for identical adjacent blocks.
"""

import logging
from typing import List, Optional, Tuple, Set

logger = logging.getLogger(__name__)

def check_for_potential_duplication(
    file_lines: List[str],
    hunk_position: int,
    old_lines: List[str],
    new_lines: List[str]
) -> bool:
    """
    Check if applying this hunk at this position would create duplication.
    
    Returns True if duplication is likely, False otherwise.
    """
    if not old_lines or not new_lines:
        return False
    
    # Get the lines that would be added (lines in new but not in old)
    added_lines = []
    old_stripped = [line.strip() for line in old_lines]
    
    for new_line in new_lines:
        new_stripped = new_line.strip()
        if new_stripped and new_stripped not in old_stripped:
            added_lines.append(new_stripped)
    
    if not added_lines:
        return False
    
    # Only check for very specific duplication patterns that are problematic
    # Focus on simple lines that are likely to cause confusion
    problematic_lines = []
    for line in added_lines:
        if (line in ['if value is None:', 'return None', 'if len(value) == 0:'] or
            line.startswith('if value is None') or
            line.startswith('if not isinstance(value, str)')):
            problematic_lines.append(line)
    
    if not problematic_lines:
        return False  # No problematic lines, allow the change
    
    logger.debug(f"Checking for duplication of problematic lines: {problematic_lines}")
    
    # Check if any of the problematic lines already exist very close by
    search_radius = 5  # Much smaller radius - only check very close lines
    start_search = max(0, hunk_position - search_radius)
    end_search = min(len(file_lines), hunk_position + len(old_lines) + search_radius)
    
    for problematic_line in problematic_lines:
        nearby_count = 0
        for i in range(start_search, end_search):
            if i >= hunk_position and i < hunk_position + len(old_lines):
                continue  # Skip the area where we're applying the hunk
            
            if i < len(file_lines) and file_lines[i].strip() == problematic_line:
                nearby_count += 1
        
        # Only flag as duplication if the same line appears multiple times nearby
        if nearby_count >= 2:
            logger.warning(f"Potential duplication detected: line '{problematic_line}' appears {nearby_count} times nearby")
            return True
    
    return False

def find_safe_position_for_hunk(
    file_lines: List[str],
    old_lines: List[str],
    new_lines: List[str],
    suggested_position: int
) -> Tuple[Optional[int], float]:
    """
    Find a safe position for applying a hunk that won't cause duplication.
    """
    if not old_lines:
        return suggested_position, 1.0
    
    # First check if the suggested position is safe
    if not check_for_potential_duplication(file_lines, suggested_position, old_lines, new_lines):
        # Check if the old lines actually match at the suggested position
        if (suggested_position >= 0 and 
            suggested_position + len(old_lines) <= len(file_lines)):
            
            candidate_lines = file_lines[suggested_position:suggested_position + len(old_lines)]
            match_count = sum(1 for old, cand in zip(old_lines, candidate_lines)
                             if old.strip() == cand.strip())
            match_ratio = match_count / len(old_lines) if old_lines else 0.0
            
            if match_ratio >= 0.7:  # Good match and no duplication risk
                logger.debug(f"Suggested position {suggested_position} is safe with match ratio {match_ratio:.3f}")
                return suggested_position, match_ratio
    
    # Look for alternative positions that are safe
    search_radius = 10
    start_pos = max(0, suggested_position - search_radius)
    end_pos = min(len(file_lines) - len(old_lines), suggested_position + search_radius)
    
    best_pos = None
    best_score = 0.0
    
    for pos in range(start_pos, end_pos + 1):
        if pos + len(old_lines) > len(file_lines):
            continue
        
        # Check if this position would cause duplication
        if check_for_potential_duplication(file_lines, pos, old_lines, new_lines):
            logger.debug(f"Position {pos} would cause duplication, skipping")
            continue
        
        # Check how well the old lines match at this position
        candidate_lines = file_lines[pos:pos + len(old_lines)]
        match_count = sum(1 for old, cand in zip(old_lines, candidate_lines)
                         if old.strip() == cand.strip())
        match_ratio = match_count / len(old_lines) if old_lines else 0.0
        
        # Prefer positions closer to suggested
        distance_penalty = abs(pos - suggested_position) / max(search_radius, 1)
        adjusted_score = match_ratio * (1.0 - distance_penalty * 0.3)
        
        logger.debug(f"Position {pos}: match_ratio={match_ratio:.3f}, "
                    f"distance_penalty={distance_penalty:.3f}, "
                    f"adjusted_score={adjusted_score:.3f}")
        
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_pos = pos
    
    if best_pos is not None and best_score >= 0.5:
        logger.debug(f"Found safe position {best_pos} with score {best_score:.3f}")
        return best_pos, best_score
    
    logger.warning(f"No safe position found, best score was {best_score:.3f}")
    return None, 0.0
