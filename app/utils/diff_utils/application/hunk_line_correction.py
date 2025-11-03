"""
Utility to detect and correct incorrect hunk line numbers using context matching.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def extract_context_from_hunk(hunk: Dict[str, Any]) -> List[str]:
    """Extract the context lines that should match the original file."""
    context = []
    
    # Try old_block first (most reliable)
    old_block = hunk.get('old_block', [])
    if old_block:
        for line in old_block:
            if isinstance(line, str):
                # Remove diff markers
                clean = line[1:] if line and line[0] in ' -' else line
                context.append(clean.rstrip('\n\r'))
        return context
    
    # Fallback to removed_lines
    removed = hunk.get('removed_lines', [])
    if removed:
        return [line.rstrip('\n\r') for line in removed if line]
    
    return []


def normalize_for_matching(line: str) -> str:
    """Normalize line for matching - strip leading/trailing whitespace."""
    return line.strip()


def find_best_match_position(context: List[str], file_lines: List[str]) -> Optional[Tuple[int, float]]:
    """
    Find best position in file for given context using fuzzy matching.
    Returns (line_number, confidence) or None.
    """
    if not context or not file_lines:
        return None
    
    # Normalize context for matching
    norm_context = [normalize_for_matching(line) for line in context]
    
    best_ratio = 0.0
    best_pos = None
    context_len = len(context)
    
    # Search entire file for best match
    for i in range(len(file_lines) - context_len + 1):
        segment = [normalize_for_matching(line.rstrip('\n\r')) for line in file_lines[i:i + context_len]]
        ratio = SequenceMatcher(None, norm_context, segment).ratio()
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = i
    
    if best_ratio > 0.7:  # Require 70% match
        return (best_pos, best_ratio)
    
    return None


def correct_hunk_line_numbers(hunks: List[Dict[str, Any]], file_lines: List[str]) -> List[Dict[str, Any]]:
    """
    Correct hunk line numbers by finding best context matches in the file.
    """
    if not hunks or not file_lines:
        return hunks
    
    corrected = []
    corrections = 0
    
    for i, hunk in enumerate(hunks, 1):
        old_start = hunk.get('old_start', 1)
        context = extract_context_from_hunk(hunk)
        
        if not context:
            corrected.append(hunk)
            continue
        
        # Find best match
        result = find_best_match_position(context, file_lines)
        
        if result:
            pos, confidence = result
            new_start = pos + 1  # Convert to 1-based
            
            # Correct if different and high confidence
            if new_start != old_start and confidence > 0.85:
                new_hunk = hunk.copy()
                new_hunk['old_start'] = new_start
                corrected.append(new_hunk)
                corrections += 1
                logger.info(f"Hunk {i}: corrected line {old_start} → {new_start} (confidence {confidence:.2f})")
            else:
                corrected.append(hunk)
        else:
            corrected.append(hunk)
    
    if corrections:
        logger.info(f"Corrected {corrections}/{len(hunks)} hunk line numbers")
    
    return corrected
