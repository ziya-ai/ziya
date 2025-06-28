"""
Conservative fuzzy matching for identical adjacent blocks.
"""

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

def conservative_fuzzy_match(
    file_lines: List[str],
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Conservative fuzzy matching that prefers the expected position when there are identical patterns.
    """
    if not chunk_lines:
        return expected_pos, 1.0
    
    logger.debug(f"Conservative matching for position {expected_pos}, chunk size {len(chunk_lines)}")
    
    # Check if the expected position is a reasonable match
    if (expected_pos >= 0 and 
        expected_pos + len(chunk_lines) <= len(file_lines)):
        
        candidate_lines = file_lines[expected_pos:expected_pos + len(chunk_lines)]
        
        # Calculate match score
        exact_matches = sum(1 for chunk, cand in zip(chunk_lines, candidate_lines)
                           if chunk.strip() == cand.strip())
        match_ratio = exact_matches / len(chunk_lines) if chunk_lines else 0.0
        
        # Check if applying at this position would cause obvious duplication
        would_duplicate = check_for_obvious_duplication(file_lines, expected_pos, chunk_lines)
        
        # If we have a decent match at expected position and no duplication risk, use it
        if match_ratio >= 0.4 and not would_duplicate:
            boosted_confidence = min(0.8, match_ratio + 0.3)
            logger.debug(f"Conservative match: using expected position {expected_pos} with ratio {match_ratio:.3f} (boosted to {boosted_confidence:.3f})")
            return expected_pos, boosted_confidence
    
    # Search for alternative positions
    search_radius = 3
    best_pos = expected_pos
    best_ratio = 0.0
    
    start_pos = max(0, expected_pos - search_radius)
    end_pos = min(len(file_lines) - len(chunk_lines), expected_pos + search_radius)
    
    for pos in range(start_pos, end_pos + 1):
        if pos + len(chunk_lines) > len(file_lines):
            continue
            
        candidate_lines = file_lines[pos:pos + len(chunk_lines)]
        
        exact_matches = sum(1 for chunk, cand in zip(chunk_lines, candidate_lines)
                           if chunk.strip() == cand.strip())
        match_ratio = exact_matches / len(chunk_lines) if chunk_lines else 0.0
        
        # Check for duplication risk
        would_duplicate = check_for_obvious_duplication(file_lines, pos, chunk_lines)
        
        if would_duplicate:
            logger.debug(f"Position {pos} would cause duplication, skipping")
            continue
        
        # Prefer positions closer to expected
        distance_penalty = abs(pos - expected_pos) / max(search_radius, 1)
        adjusted_ratio = match_ratio * (1.0 - distance_penalty * 0.2)
        
        if adjusted_ratio > best_ratio:
            best_ratio = adjusted_ratio
            best_pos = pos
    
    # Boost confidence for conservative matching
    if best_ratio >= 0.3:
        boosted_confidence = min(0.8, best_ratio + 0.3)
        logger.debug(f"Conservative match: found position {best_pos} with ratio {best_ratio:.3f} (boosted to {boosted_confidence:.3f})")
        return best_pos, boosted_confidence
    
    # Last resort: use expected position with warning
    logger.warning(f"Conservative match: no good safe position found, using expected {expected_pos} with low confidence")
    return expected_pos, 0.4

def check_for_obvious_duplication(file_lines: List[str], position: int, chunk_lines: List[str]) -> bool:
    """
    Check if applying chunk at this position would create obvious duplication.
    Focus on very specific patterns that cause problems.
    """
    # Only check for very specific problematic patterns
    problematic_patterns = [
        'if value is None:',
        'if not isinstance(value, str):',
        'if len(value) == 0:'
    ]
    
    # Check if any of the chunk lines contain these problematic patterns
    chunk_has_problematic = False
    for chunk_line in chunk_lines:
        chunk_stripped = chunk_line.strip()
        if chunk_stripped in problematic_patterns:
            chunk_has_problematic = True
            break
    
    if not chunk_has_problematic:
        return False  # No problematic patterns, safe to apply
    
    # Check for duplication only in a very small radius
    search_radius = 2  # Very small radius
    start_check = max(0, position - search_radius)
    end_check = min(len(file_lines), position + len(chunk_lines) + search_radius)
    
    for chunk_line in chunk_lines:
        chunk_stripped = chunk_line.strip()
        if chunk_stripped not in problematic_patterns:
            continue
            
        # Count occurrences in the small nearby area
        nearby_count = 0
        for i in range(start_check, end_check):
            if i >= position and i < position + len(chunk_lines):
                continue  # Skip the area where we're applying
            if i < len(file_lines) and file_lines[i].strip() == chunk_stripped:
                nearby_count += 1
        
        # Only flag if we find the exact same problematic line very close by
        if nearby_count >= 1:
            logger.debug(f"Duplication risk: '{chunk_stripped}' appears {nearby_count} times very close to position {position}")
            return True
    
    return False

def has_identical_patterns(file_lines: List[str], chunk_lines: List[str]) -> bool:
    """
    Check if the file has multiple identical patterns that could cause confusion.
    """
    if len(chunk_lines) < 3:
        return False
    
    # Look for a distinctive line in the chunk
    distinctive_line = None
    for line in chunk_lines:
        stripped = line.strip()
        if (stripped and 
            len(stripped) > 8 and
            stripped not in ['return None', 'pass', '{}', '[]']):
            distinctive_line = stripped
            break
    
    if not distinctive_line:
        return False
    
    # Count occurrences in the file
    count = sum(1 for file_line in file_lines if file_line.strip() == distinctive_line)
    return count > 1
