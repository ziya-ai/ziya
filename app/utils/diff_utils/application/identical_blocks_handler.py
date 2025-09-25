"""
Specialized handler for identical adjacent blocks in diff application.
"""

import logging
import difflib
from typing import List, Optional, Tuple, Dict

logger = logging.getLogger(__name__)

def detect_identical_adjacent_blocks(file_lines: List[str], hunks: List[Dict]) -> bool:
    """
    Detect if this diff involves identical adjacent blocks that could cause confusion.
    
    Args:
        file_lines: The original file content
        hunks: List of hunks to be applied
        
    Returns:
        True if identical adjacent blocks are detected
    """
    # Look for patterns that appear multiple times in the file
    patterns_found = {}
    
    for hunk in hunks:
        # Extract the "old" lines from the hunk (lines being replaced/removed)
        old_lines = []
        for line in hunk.get('lines', []):
            if line.startswith('-') or line.startswith(' '):
                old_lines.append(line[1:] if line.startswith(('-', ' ')) else line)
        
        if len(old_lines) >= 3:  # Only consider substantial patterns
            # Create a signature for this pattern
            pattern_signature = tuple(line.strip() for line in old_lines[:5])  # First 5 lines
            
            if pattern_signature in patterns_found:
                patterns_found[pattern_signature] += 1
            else:
                patterns_found[pattern_signature] = 1
    
    # Check if any pattern appears multiple times in the file
    for pattern, count in patterns_found.items():
        if count > 1:
            logger.debug(f"Detected identical pattern appearing {count} times: {pattern[:2]}...")
            return True
    
    # Also check if similar patterns exist in the file
    for hunk in hunks:
        old_start = hunk.get('old_start', 0)
        old_lines = []
        for line in hunk.get('lines', []):
            if line.startswith('-') or line.startswith(' '):
                old_lines.append(line[1:] if line.startswith(('-', ' ')) else line)
        
        if len(old_lines) >= 3:
            # Look for similar patterns elsewhere in the file
            similar_positions = find_similar_patterns(file_lines, old_lines, old_start - 1)
            if len(similar_positions) > 1:
                logger.debug(f"Found {len(similar_positions)} similar patterns for hunk at {old_start}")
                return True
    
    return False

def find_similar_patterns(file_lines: List[str], pattern: List[str], exclude_pos: int) -> List[int]:
    """
    Find positions in the file where similar patterns occur.
    """
    similar_positions = []
    
    # Look for the first distinctive line of the pattern
    if not pattern:
        return similar_positions
    
    first_line = pattern[0].strip()
    if not first_line:
        return similar_positions
    
    # Find all occurrences of the first line
    for i, file_line in enumerate(file_lines):
        if i == exclude_pos:
            continue
            
        if file_line.strip() == first_line:
            # Check if the following lines also match
            match_score = 0
            total_lines = min(len(pattern), len(file_lines) - i)
            
            for j in range(total_lines):
                if i + j >= len(file_lines):
                    break
                    
                pattern_line = pattern[j].strip()
                file_line = file_lines[i + j].strip()
                
                if pattern_line == file_line:
                    match_score += 1
                elif pattern_line in file_line or file_line in pattern_line:
                    match_score += 0.5
            
            # Consider it similar if at least 60% of lines match
            if total_lines > 0 and (match_score / total_lines) >= 0.6:
                similar_positions.append(i)
    
    return similar_positions

def apply_hunks_with_context_awareness(
    file_lines: List[str], 
    hunks: List[Dict],
    use_enhanced_matching: bool = True
) -> Tuple[List[str], List[Dict]]:
    """
    Apply hunks with enhanced context awareness for identical adjacent blocks.
    
    Args:
        file_lines: Original file content
        hunks: List of hunks to apply
        use_enhanced_matching: Whether to use enhanced matching
        
    Returns:
        Tuple of (modified_file_lines, hunk_results)
    """
    if not use_enhanced_matching or not detect_identical_adjacent_blocks(file_lines, hunks):
        # Fall back to normal processing
        return None, []
    
    logger.info("Applying enhanced context-aware processing for identical adjacent blocks")
    
    modified_lines = file_lines.copy()
    hunk_results = []
    
    # Sort hunks by their original position to apply them in order
    sorted_hunks = sorted(hunks, key=lambda h: h.get('old_start', 0))
    
    line_offset = 0  # Track how line numbers shift as we apply changes
    
    for hunk in sorted_hunks:
        old_start = hunk.get('old_start', 0) - 1  # Convert to 0-based
        old_count = hunk.get('old_count', 0)
        
        # Adjust position based on previous changes
        adjusted_start = old_start + line_offset
        
        # Extract old and new lines from the hunk
        old_lines = []
        new_lines = []
        
        for line in hunk.get('lines', []):
            if line.startswith('-'):
                old_lines.append(line[1:])
            elif line.startswith('+'):
                new_lines.append(line[1:])
            elif line.startswith(' '):
                # Context line - appears in both old and new
                old_lines.append(line[1:])
                new_lines.append(line[1:])
        
        # Verify that the old lines match at the expected position
        if adjusted_start + len(old_lines) <= len(modified_lines):
            actual_lines = modified_lines[adjusted_start:adjusted_start + len(old_lines)]
            
            # Check for exact match
            exact_match = True
            for i, (expected, actual) in enumerate(zip(old_lines, actual_lines)):
                if expected.strip() != actual.strip():
                    exact_match = False
                    break
            
            if exact_match:
                # Apply the change
                modified_lines[adjusted_start:adjusted_start + len(old_lines)] = new_lines
                line_offset += len(new_lines) - len(old_lines)
                
                hunk_results.append({
                    'hunk_id': hunk.get('number', 0),
                    'status': 'succeeded',
                    'position': adjusted_start,
                    'method': 'context_aware_exact'
                })
                
                logger.debug(f"Applied hunk #{hunk.get('number', 0)} at position {adjusted_start} (exact match)")
                continue
        
        # If exact match failed, try context-aware fuzzy matching
        best_pos, confidence = find_best_position_with_context(
            modified_lines, old_lines, new_lines, adjusted_start, hunk
        )
        
        if best_pos is not None and confidence >= 0.8:
            # Apply the change at the best position
            modified_lines[best_pos:best_pos + len(old_lines)] = new_lines
            line_offset += len(new_lines) - len(old_lines)
            
            hunk_results.append({
                'hunk_id': hunk.get('number', 0),
                'status': 'succeeded',
                'position': best_pos,
                'method': 'context_aware_fuzzy',
                'confidence': confidence
            })
            
            logger.debug(f"Applied hunk #{hunk.get('number', 0)} at position {best_pos} (fuzzy match, confidence: {confidence:.3f})")
        else:
            # Failed to apply
            hunk_results.append({
                'hunk_id': hunk.get('number', 0),
                'status': 'failed',
                'position': None,
                'method': 'context_aware_failed',
                'confidence': confidence if best_pos is not None else 0.0
            })
            
            logger.warning(f"Failed to apply hunk #{hunk.get('number', 0)} (confidence: {confidence if best_pos is not None else 0.0:.3f})")
    
    return modified_lines, hunk_results

def find_best_position_with_context(
    file_lines: List[str],
    old_lines: List[str],
    new_lines: List[str],
    expected_pos: int,
    hunk: Dict
) -> Tuple[Optional[int], float]:
    """
    Find the best position to apply a hunk using extended context analysis.
    """
    if not old_lines:
        return expected_pos, 1.0
    
    # Get extended context around the expected position
    context_radius = 10
    search_radius = 5
    
    best_pos = None
    best_confidence = 0.0
    
    # Search in a window around the expected position
    start_search = max(0, expected_pos - search_radius)
    end_search = min(len(file_lines) - len(old_lines), expected_pos + search_radius)
    
    for pos in range(start_search, end_search + 1):
        if pos + len(old_lines) > len(file_lines):
            continue
        
        # Get the candidate lines
        candidate_lines = file_lines[pos:pos + len(old_lines)]
        
        # Calculate direct similarity
        direct_similarity = calculate_line_similarity(old_lines, candidate_lines)
        
        # Calculate context similarity (lines before and after)
        context_before = file_lines[max(0, pos - context_radius):pos]
        context_after = file_lines[pos + len(old_lines):min(len(file_lines), pos + len(old_lines) + context_radius)]
        
        expected_context_before = file_lines[max(0, expected_pos - context_radius):expected_pos]
        expected_context_after = file_lines[expected_pos + len(old_lines):min(len(file_lines), expected_pos + len(old_lines) + context_radius)]
        
        context_before_sim = calculate_line_similarity(expected_context_before, context_before)
        context_after_sim = calculate_line_similarity(expected_context_after, context_after)
        
        # Combined confidence score
        combined_confidence = (
            direct_similarity * 0.6 +
            context_before_sim * 0.2 +
            context_after_sim * 0.2
        )
        
        # Penalty for distance from expected position
        distance_penalty = abs(pos - expected_pos) / max(len(file_lines), 1)
        combined_confidence *= (1.0 - distance_penalty * 0.1)
        
        if combined_confidence > best_confidence:
            best_confidence = combined_confidence
            best_pos = pos
    
    return best_pos, best_confidence

def calculate_line_similarity(lines1: List[str], lines2: List[str]) -> float:
    """
    Calculate similarity between two lists of lines.
    """
    if not lines1 and not lines2:
        return 1.0
    if not lines1 or not lines2:
        return 0.0
    
    # Use difflib to calculate similarity
    text1 = '\n'.join(line.strip() for line in lines1)
    text2 = '\n'.join(line.strip() for line in lines2)
    
    return difflib.SequenceMatcher(None, text1, text2).ratio()
