"""
Enhanced fuzzy matching utilities for diff application.

This module provides improved fuzzy matching algorithms for finding the best position
to apply a hunk in a file, with multiple matching strategies and adaptive confidence thresholds.
"""

from typing import List, Optional, Tuple, Dict, Any, Callable
import difflib
import re
import logging
from itertools import zip_longest

from ..core.config import get_search_radius, get_confidence_threshold
from ..validation.validators import normalize_line_for_comparison
from ..application.comment_handler import is_comment_line, remove_trailing_comment

# Configure logging
logger = logging.getLogger(__name__)

def find_best_chunk_position_enhanced(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position in file_lines to apply chunk_lines using multiple strategies.
    
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
    
    # Calculate search range
    start_pos = max(0, expected_pos - search_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + search_radius) if expected_pos is not None else len(file_lines)
    
    # Define multiple matching strategies
    strategies = [
        # Strategy 1: Standard sequence matching (original method)
        lambda f_slice, c_slice: difflib.SequenceMatcher(None, ''.join(f_slice), ''.join(c_slice)).ratio(),
        
        # Strategy 2: Line-by-line matching (average of line ratios)
        lambda f_slice, c_slice: sum(difflib.SequenceMatcher(None, f, c).ratio() 
                                    for f, c in zip_longest(f_slice, c_slice, fillvalue='')) 
                                / max(len(f_slice), len(c_slice)),
        
        # Strategy 3: Normalized content matching (strip whitespace)
        lambda f_slice, c_slice: difflib.SequenceMatcher(None, 
                                                       ''.join(line.strip() for line in f_slice), 
                                                       ''.join(line.strip() for line in c_slice)).ratio(),
                                                       
        # Strategy 4: Token-based matching (split into words and compare)
        lambda f_slice, c_slice: difflib.SequenceMatcher(None,
                                                       ' '.join(re.findall(r'\w+', ''.join(f_slice))),
                                                       ' '.join(re.findall(r'\w+', ''.join(c_slice)))).ratio(),
                                                       
        # Strategy 5: Structure-based matching (compare indentation patterns)
        lambda f_slice, c_slice: compare_indentation_patterns(f_slice, c_slice),
        
        # Strategy 6: Comment-aware matching (ignore comment differences)
        lambda f_slice, c_slice: calculate_comment_aware_similarity(f_slice, c_slice)
    ]
    
    best_pos = expected_pos
    best_ratio = 0.0
    best_strategy = -1
    
    # Search for the best match using all strategies
    for pos in range(start_pos, end_pos):
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        
        # Try all strategies and take the best ratio
        for i, strategy in enumerate(strategies):
            try:
                ratio = strategy(file_slice, chunk_lines)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_pos = pos
                    best_strategy = i
                    logger.debug(f"New best match at pos={pos} with ratio={ratio:.4f} using strategy {i}")
            except Exception as e:
                logger.debug(f"Strategy {i} failed at pos={pos}: {str(e)}")
    
    # Adaptive confidence threshold based on content type
    base_threshold = get_confidence_threshold('medium')
    confidence_threshold = base_threshold
    
    # Adjust threshold for special cases
    if len(chunk_lines) <= 3:
        # For very short chunks, lower the threshold
        confidence_threshold *= 0.7
        logger.debug(f"Lowering threshold to {confidence_threshold:.4f} for short chunk ({len(chunk_lines)} lines)")
    
    # Check if this is a whitespace-only change
    if best_pos is not None:
        whitespace_only = is_whitespace_only_change(file_lines[best_pos:best_pos+len(chunk_lines)], chunk_lines)
        if whitespace_only:
            # For whitespace-only changes, lower the threshold significantly
            confidence_threshold *= 0.5
            logger.debug(f"Lowering threshold to {confidence_threshold:.4f} for whitespace-only change")
    
    # Log the best match details
    if best_pos is not None:
        logger.debug(f"Best match: pos={best_pos}, ratio={best_ratio:.4f}, strategy={best_strategy}, threshold={confidence_threshold:.4f}")
    
    # Return None if confidence is too low
    if best_ratio < confidence_threshold and (best_pos != expected_pos or expected_pos is None):
        return None, best_ratio
    
    return best_pos, best_ratio

def calculate_comment_aware_similarity(file_slice: List[str], chunk_lines: List[str]) -> float:
    """
    Calculate similarity while ignoring differences in comments.
    
    Args:
        file_slice: A slice of the file content
        chunk_lines: The chunk to compare against
        
    Returns:
        A similarity ratio between 0.0 and 1.0
    """
    if not chunk_lines:
        return 1.0
    
    if len(file_slice) != len(chunk_lines):
        # If lengths differ, use a standard ratio but with reduced weight
        return difflib.SequenceMatcher(None, ''.join(file_slice), ''.join(chunk_lines)).ratio() * 0.8
    
    # Process each line pair
    match_count = 0
    comment_lines = 0
    
    for i, (file_line, chunk_line) in enumerate(zip(file_slice, chunk_lines)):
        # Check if either line is a comment
        file_is_comment = is_comment_line(file_line)
        chunk_is_comment = is_comment_line(chunk_line)
        
        if file_is_comment and chunk_is_comment:
            # Both are comments, give partial credit
            comment_lines += 1
            match_count += 0.7
        elif file_is_comment or chunk_is_comment:
            # One is a comment but not the other, give less credit
            comment_lines += 1
            match_count += 0.3
        else:
            # Neither is a comment, remove any trailing comments and compare
            file_code = remove_trailing_comment(file_line)
            chunk_code = remove_trailing_comment(chunk_line)
            
            if file_code.strip() == chunk_code.strip():
                # Exact match after removing comments
                match_count += 1.0
            else:
                # Partial match based on token similarity
                file_tokens = set(re.findall(r'\w+', file_code))
                chunk_tokens = set(re.findall(r'\w+', chunk_code))
                
                if file_tokens and chunk_tokens:
                    # Calculate Jaccard similarity
                    intersection = len(file_tokens.intersection(chunk_tokens))
                    union = len(file_tokens.union(chunk_tokens))
                    
                    if union > 0:
                        token_similarity = intersection / union
                        match_count += token_similarity
    
    # Calculate final score
    base_score = match_count / len(chunk_lines)
    
    # Boost score if most differences are in comments
    if comment_lines > 0 and (match_count / len(chunk_lines)) >= 0.7:
        comment_ratio = comment_lines / len(chunk_lines)
        # Boost more if a higher percentage of lines are comments
        boost = 0.1 * comment_ratio
        return min(0.95, base_score + boost)
    
    return base_score

def compare_indentation_patterns(file_slice: List[str], chunk_lines: List[str]) -> float:
    """
    Compare the indentation patterns of two blocks of code.
    
    Args:
        file_slice: A slice of the file content
        chunk_lines: The chunk to compare against
        
    Returns:
        A similarity ratio between 0.0 and 1.0
    """
    if not file_slice or not chunk_lines:
        return 0.0
    
    # Extract indentation patterns
    file_indents = [len(line) - len(line.lstrip()) for line in file_slice]
    chunk_indents = [len(line) - len(line.lstrip()) for line in chunk_lines]
    
    # Compare the patterns
    if not file_indents or not chunk_indents:
        return 0.0
    
    # Calculate relative indentation (differences between consecutive lines)
    file_rel_indents = [file_indents[i] - file_indents[i-1] for i in range(1, len(file_indents))]
    chunk_rel_indents = [chunk_indents[i] - chunk_indents[i-1] for i in range(1, len(chunk_indents))]
    
    # If either list is empty (only one line), return partial match
    if not file_rel_indents or not chunk_rel_indents:
        return 0.5 if file_indents[0] == chunk_indents[0] else 0.0
    
    # Compare the relative indentation patterns
    matches = sum(1 for a, b in zip_longest(file_rel_indents, chunk_rel_indents, fillvalue=None) if a == b)
    return matches / max(len(file_rel_indents), len(chunk_rel_indents))

def is_whitespace_only_change(file_slice: List[str], chunk_lines: List[str]) -> bool:
    """
    Check if the difference between file_slice and chunk_lines is only whitespace.
    
    Args:
        file_slice: A slice of the file content
        chunk_lines: The chunk to compare against
        
    Returns:
        True if the only differences are whitespace, False otherwise
    """
    if len(file_slice) != len(chunk_lines):
        return False
    
    for file_line, chunk_line in zip(file_slice, chunk_lines):
        # Compare the lines ignoring whitespace
        if file_line.strip() != chunk_line.strip():
            return False
    
    return True

def find_best_chunk_position_with_fallbacks(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float, Dict[str, Any]]:
    """
    Find the best position with multiple fallback strategies.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio, match_details)
    """
    # First try the enhanced algorithm
    best_pos, best_ratio = find_best_chunk_position_enhanced(file_lines, chunk_lines, expected_pos)
    
    match_details = {
        "primary_match": {
            "position": best_pos,
            "confidence": best_ratio,
            "method": "enhanced"
        },
        "fallbacks_tried": []
    }
    
    # If the enhanced algorithm found a good match, return it
    if best_pos is not None:
        return best_pos, best_ratio, match_details
    
    # Otherwise, try fallback strategies
    fallback_strategies = [
        # Fallback 1: Try with normalized lines
        ("normalized", lambda f, c, p: find_best_chunk_position_normalized(f, c, p)),
        
        # Fallback 2: Try with relaxed matching
        ("relaxed", lambda f, c, p: find_best_chunk_position_relaxed(f, c, p)),
        
        # Fallback 3: Try with wider search radius
        ("wide_search", lambda f, c, p: find_best_chunk_position_wide_search(f, c, p)),
        
        # Fallback 4: Try with comment-aware matching
        ("comment_aware", lambda f, c, p: find_best_chunk_position_comment_aware(f, c, p))
    ]
    
    for name, strategy in fallback_strategies:
        fallback_pos, fallback_ratio = strategy(file_lines, chunk_lines, expected_pos)
        
        match_details["fallbacks_tried"].append({
            "name": name,
            "position": fallback_pos,
            "confidence": fallback_ratio
        })
        
        if fallback_pos is not None and fallback_ratio > best_ratio:
            best_pos = fallback_pos
            best_ratio = fallback_ratio
            match_details["best_match"] = {
                "position": best_pos,
                "confidence": best_ratio,
                "method": name
            }
            
            # If we found a good match, return it
            if best_ratio >= get_confidence_threshold('low'):
                return best_pos, best_ratio, match_details
    
    # Return the best match we found, even if it's below the threshold
    return best_pos, best_ratio, match_details

def find_best_chunk_position_normalized(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position using normalized lines.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
    """
    # Normalize all lines
    normalized_file_lines = [normalize_line_for_comparison(line) for line in file_lines]
    normalized_chunk_lines = [normalize_line_for_comparison(line) for line in chunk_lines]
    
    # Use the original algorithm with normalized lines
    from ..application.fuzzy_match import find_best_chunk_position
    return find_best_chunk_position(normalized_file_lines, normalized_chunk_lines, expected_pos)

def find_best_chunk_position_relaxed(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position with relaxed matching criteria.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
    """
    # Get search radius from config
    search_radius = get_search_radius()
    
    # Calculate search range
    start_pos = max(0, expected_pos - search_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + search_radius) if expected_pos is not None else len(file_lines)
    
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Search for the best match using relaxed criteria
    for pos in range(start_pos, end_pos):
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        
        # Count matching lines (ignoring whitespace)
        match_count = sum(1 for f, c in zip(file_slice, chunk_lines) 
                         if f.strip() == c.strip())
        
        # Calculate ratio based on matching lines
        ratio = match_count / len(chunk_lines) if chunk_lines else 0.0
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
    
    # Use a lower threshold for relaxed matching
    threshold = get_confidence_threshold('low')
    
    # Return None if confidence is too low
    if best_ratio < threshold:
        return None, best_ratio
    
    return best_pos, best_ratio

def find_best_chunk_position_wide_search(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position with a wider search radius.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
    """
    # Use a wider search radius
    wide_radius = get_search_radius() * 3
    
    # Calculate search range
    start_pos = max(0, expected_pos - wide_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + wide_radius) if expected_pos is not None else len(file_lines)
    
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Search for the best match in the wider range
    for pos in range(start_pos, end_pos):
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        
        # Use sequence matcher for comparison
        ratio = difflib.SequenceMatcher(None, 
                                      ''.join(file_slice), 
                                      ''.join(chunk_lines)).ratio()
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
    
    # Use a very low threshold for wide search
    threshold = get_confidence_threshold('very_low')
    
    # Return None if confidence is too low
    if best_ratio < threshold:
        return None, best_ratio
    
    return best_pos, best_ratio

def find_best_chunk_position_comment_aware(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position with comment-aware matching.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
    """
    # Get search radius from config
    search_radius = get_search_radius()
    
    # Calculate search range
    start_pos = max(0, expected_pos - search_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + search_radius) if expected_pos is not None else len(file_lines)
    
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Search for the best match using comment-aware matching
    for pos in range(start_pos, end_pos):
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        
        # Use comment-aware similarity
        ratio = calculate_comment_aware_similarity(file_slice, chunk_lines)
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
    
    # Use a lower threshold for comment-aware matching
    threshold = get_confidence_threshold('low')
    
    # Return None if confidence is too low
    if best_ratio < threshold:
        return None, best_ratio
    
    return best_pos, best_ratio
