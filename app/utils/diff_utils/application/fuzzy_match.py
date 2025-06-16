"""
Fuzzy matching utilities for diff application.
"""

import difflib
import logging
import re
from typing import List, Optional, Tuple
import os
from ..core.config import get_search_radius, get_confidence_threshold
from ..application.whitespace_handler import is_whitespace_only_diff, normalize_whitespace_for_comparison

# Configure logging
logger = logging.getLogger(__name__)

def find_best_chunk_position(
    file_lines: List[str], 
    chunk_lines: List[str], 
    expected_pos: int
) -> Tuple[Optional[int], float]:
    """
    Find the best position in file_lines to apply chunk_lines.
    Enhanced to handle indentation changes and formatting differences.
    
    Args:
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
        If no good match is found, position will be None
    """
    logger.debug(f"ENHANCED FUZZY MATCH: Processing chunk with {len(chunk_lines)} lines at expected pos {expected_pos}")
    
    if not chunk_lines:
        return expected_pos, 1.0  # Empty chunks match perfectly at the expected position

    # Get search radius from config
    search_radius = get_search_radius()
    
    # Get confidence threshold from config (using 'medium' level)
    confidence_threshold = get_confidence_threshold('medium')
    
    logger.debug(f"ENHANCED FUZZY MATCH: Using confidence threshold {confidence_threshold}")
    
    # Calculate search range
    start_pos = max(0, expected_pos - search_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + search_radius) if expected_pos is not None else len(file_lines)
    
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Check if this is a whitespace-only change
    is_whitespace_change = False
    
    # Extract content lines (not markers)
    content_lines = [line[1:] if line.startswith(('+', '-', ' ')) else line for line in chunk_lines]
    
    # Compare content ignoring whitespace
    content_no_ws = [''.join(line.split()) for line in content_lines if line.strip()]
    if len(set(content_no_ws)) <= 1 and content_no_ws:
        is_whitespace_change = True
        logger.debug(f"Detected whitespace-only change in chunk")
    
    # Enhanced matching strategies for better handling of indentation changes
    def calculate_enhanced_similarity(chunk_lines: List[str], file_slice: List[str]) -> float:
        """Calculate similarity using multiple strategies and return the best ratio."""
        ratios = []
        
        # Strategy 1: Direct comparison
        chunk_text = '\n'.join(chunk_lines)
        file_text = '\n'.join(file_slice)
        direct_ratio = difflib.SequenceMatcher(None, chunk_text, file_text).ratio()
        ratios.append(direct_ratio)
        
        # Strategy 2: Normalized whitespace comparison
        chunk_normalized = '\n'.join(normalize_whitespace_for_comparison(line) for line in chunk_lines)
        file_normalized = '\n'.join(normalize_whitespace_for_comparison(line) for line in file_slice)
        normalized_ratio = difflib.SequenceMatcher(None, chunk_normalized, file_normalized).ratio()
        ratios.append(normalized_ratio)
        
        # Strategy 3: Content-only comparison (ignoring all whitespace)
        chunk_content = ''.join(''.join(line.split()) for line in chunk_lines)
        file_content = ''.join(''.join(line.split()) for line in file_slice)
        if chunk_content and file_content:
            content_ratio = difflib.SequenceMatcher(None, chunk_content, file_content).ratio()
            ratios.append(content_ratio)
        
        # Strategy 4: Token-based comparison (split by whitespace and compare tokens)
        chunk_tokens = ' '.join('\n'.join(chunk_lines).split())
        file_tokens = ' '.join('\n'.join(file_slice).split())
        if chunk_tokens and file_tokens:
            token_ratio = difflib.SequenceMatcher(None, chunk_tokens, file_tokens).ratio()
            ratios.append(token_ratio)
        
        # Strategy 5: Line-by-line content comparison (ignoring indentation)
        if len(chunk_lines) == len(file_slice):
            line_matches = 0
            for chunk_line, file_line in zip(chunk_lines, file_slice):
                chunk_stripped = ''.join(chunk_line.split())
                file_stripped = ''.join(file_line.split())
                if chunk_stripped == file_stripped:
                    line_matches += 1
            if len(chunk_lines) > 0:
                line_ratio = line_matches / len(chunk_lines)
                ratios.append(line_ratio)
        
        # Strategy 6: Structural similarity (comparing non-empty lines only)
        chunk_non_empty = [line.strip() for line in chunk_lines if line.strip()]
        file_non_empty = [line.strip() for line in file_slice if line.strip()]
        if chunk_non_empty and file_non_empty:
            struct_ratio = difflib.SequenceMatcher(None, 
                                                 '\n'.join(chunk_non_empty), 
                                                 '\n'.join(file_non_empty)).ratio()
            ratios.append(struct_ratio)
        
        # Strategy 7: Indentation-aware comparison for code files
        # Extract the logical structure by removing leading whitespace
        chunk_logical = []
        file_logical = []
        
        for line in chunk_lines:
            # Keep the content but normalize indentation to a standard level
            stripped = line.lstrip()
            if stripped:  # Only process non-empty lines
                # Count original indentation level
                indent_level = len(line) - len(stripped)
                # Normalize to 2-space indentation for comparison
                normalized_indent = '  ' * (indent_level // 2 if indent_level > 0 else 0)
                chunk_logical.append(normalized_indent + stripped)
            else:
                chunk_logical.append('')
        
        for line in file_slice:
            stripped = line.lstrip()
            if stripped:
                indent_level = len(line) - len(stripped)
                normalized_indent = '  ' * (indent_level // 2 if indent_level > 0 else 0)
                file_logical.append(normalized_indent + stripped)
            else:
                file_logical.append('')
        
        if chunk_logical and file_logical:
            logical_ratio = difflib.SequenceMatcher(None, 
                                                  '\n'.join(chunk_logical), 
                                                  '\n'.join(file_logical)).ratio()
            ratios.append(logical_ratio)
        
        # Strategy 8: Semantic similarity for code (ignoring formatting entirely)
        # Remove all whitespace, punctuation spacing, and compare just the semantic content
        import re
        
        def normalize_code_semantics(text):
            # Remove all whitespace
            no_ws = re.sub(r'\s+', '', text)
            # Normalize common code patterns
            no_ws = re.sub(r'{\s*}', '{}', no_ws)  # Empty blocks
            no_ws = re.sub(r';\s*', ';', no_ws)    # Semicolons
            no_ws = re.sub(r',\s*', ',', no_ws)    # Commas
            return no_ws
        
        chunk_semantic = normalize_code_semantics('\n'.join(chunk_lines))
        file_semantic = normalize_code_semantics('\n'.join(file_slice))
        
        if chunk_semantic and file_semantic:
            semantic_ratio = difflib.SequenceMatcher(None, chunk_semantic, file_semantic).ratio()
            ratios.append(semantic_ratio)
        
        # Return the best ratio from all strategies
        best_ratio = max(ratios) if ratios else 0.0
        
        # Log the different ratios for debugging
        logger.debug(f"Similarity ratios - Direct: {direct_ratio:.3f}, "
                    f"Normalized: {normalized_ratio:.3f}, "
                    f"Content: {ratios[2] if len(ratios) > 2 else 'N/A':.3f}, "
                    f"Token: {ratios[3] if len(ratios) > 3 else 'N/A':.3f}, "
                    f"Line: {ratios[4] if len(ratios) > 4 else 'N/A':.3f}, "
                    f"Struct: {ratios[5] if len(ratios) > 5 else 'N/A':.3f}, "
                    f"Logical: {ratios[6] if len(ratios) > 6 else 'N/A':.3f}, "
                    f"Semantic: {ratios[7] if len(ratios) > 7 else 'N/A':.3f}, "
                    f"Best: {best_ratio:.3f}")
        
        return best_ratio
    
    # Search for the best match within the search radius
    for pos in range(start_pos, end_pos):
        # Make sure we don't go out of bounds
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        # Get the slice of file_lines to compare
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        
        # Calculate enhanced similarity ratio
        ratio = calculate_enhanced_similarity(chunk_lines, file_slice)
        
        # Update best match if this is better
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
            logger.debug(f"Found better match at position {pos} with enhanced ratio {ratio:.3f}")
    
    # For whitespace changes or indentation-heavy content, use a more lenient threshold
    effective_threshold = confidence_threshold
    
    # Detect if this looks like an indentation change by checking if content similarity is high
    # but direct similarity is low
    if best_ratio > 0:
        # Calculate a quick content-only ratio for threshold adjustment
        chunk_content_quick = ''.join(''.join(line.split()) for line in chunk_lines)
        best_file_slice = file_lines[best_pos:best_pos + len(chunk_lines)] if best_pos is not None else []
        file_content_quick = ''.join(''.join(line.split()) for line in best_file_slice)
        
        if chunk_content_quick and file_content_quick:
            content_similarity = difflib.SequenceMatcher(None, chunk_content_quick, file_content_quick).ratio()
            
            # If content similarity is high but overall similarity is low, likely an indentation change
            if content_similarity > 0.8 and best_ratio < confidence_threshold:
                effective_threshold = confidence_threshold * 0.6  # More lenient for indentation changes
                logger.debug(f"Detected likely indentation change (content: {content_similarity:.3f}, overall: {best_ratio:.3f})")
                logger.debug(f"Using lenient threshold {effective_threshold:.3f} for indentation change")
            elif content_similarity > 0.9:
                effective_threshold = confidence_threshold * 0.7  # Very lenient for near-identical content
                logger.debug(f"Using very lenient threshold {effective_threshold:.3f} for near-identical content")
    
    if is_whitespace_change:
        effective_threshold = min(effective_threshold, confidence_threshold * 0.7)
        logger.debug(f"Using lenient threshold {effective_threshold:.3f} for whitespace change")
    
    # Additional check: if the best ratio is close to the threshold, be more lenient
    if best_ratio >= confidence_threshold * 0.8:  # Within 80% of threshold
        effective_threshold = confidence_threshold * 0.8
        logger.debug(f"Using lenient threshold {effective_threshold:.3f} for near-threshold match")
    
    # Return None if confidence is too low
    if best_ratio < effective_threshold:
        logger.error(f"Best match has low confidence (ratio={best_ratio:.3f}, threshold={effective_threshold:.3f}) near {expected_pos}, skipping.")
        return None, best_ratio
    
    logger.debug(f"Found acceptable match at position {best_pos} with ratio {best_ratio:.3f}")
    return best_pos, best_ratio
