"""
Utility to detect and correct incorrect hunk line numbers using context matching.
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def extract_function_context_from_header(header: str) -> Optional[str]:
    """
    Extract the function/method context from a hunk header.
    
    Hunk headers can include function context after the @@ markers, e.g.:
    @@ -854,14 +854,47 @@ async def continue_response_stream(continuation_state: Dict[str, Any], conversa
    
    Returns the function signature if found, None otherwise.
    """
    if not header:
        return None
    
    # Match the pattern: @@ -N,N +N,N @@ <function_context>
    match = re.match(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@\s*(.+)$', header)
    if match:
        context = match.group(1).strip()
        # Check if it looks like a function/method definition
        if context and any(kw in context for kw in ['def ', 'async def ', 'function ', 'class ']):
            return context
    return None


def validate_function_context_at_position(
    function_context: str, 
    file_lines: List[str], 
    position: int,
    search_range: int = 50
) -> bool:
    """
    Validate that the function context from the hunk header exists in the file.
    
    This prevents diffs from being applied to the wrong file when the context lines
    partially match but the function/class specified in the header doesn't exist.
    
    The function context in a hunk header (e.g., "async def continue_response_stream")
    indicates which function/class the change is within. If this function/class doesn't
    exist anywhere in the file, the diff is likely targeting the wrong file.
    
    Args:
        function_context: The function signature from the hunk header
        file_lines: The lines of the target file
        position: The target line position (0-based) - used for logging only
        search_range: Not used (kept for API compatibility)
        
    Returns:
        True if the function context is found in the file, False otherwise
    """
    if not function_context or not file_lines:
        return True  # No function context to validate
    
    # Extract the function/class name from the context
    func_name_match = re.search(r'(?:async\s+)?(?:def|function|class)\s+(\w+)', function_context)
    if not func_name_match:
        return True  # Can't extract function name, skip validation
    
    func_name = func_name_match.group(1)
    
    # Search the entire file for the function/class definition
    for i, line in enumerate(file_lines):
        # Check if this line contains the function/class definition
        # Handle both Python (def/class) and JavaScript/TypeScript (function/class) syntax
        if re.search(rf'(?:async\s+)?(?:def|function|class)\s+{re.escape(func_name)}\b', line):
            return True
    
    logger.warning(f"Function context validation failed: '{func_name}' not found in file (hunk targets line {position + 1})")
    return False


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
    
    # Try content field (from parse_unified_diff)
    content = hunk.get('content', [])
    if content:
        for line in content:
            if isinstance(line, str):
                # Remove diff markers - include context (' ') and removed ('-') lines
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


def find_best_match_position(context: List[str], file_lines: List[str], original_line: Optional[int] = None) -> Optional[Tuple[int, float]]:
    """
    Find best position in file for given context using fuzzy matching.
    Returns (line_number, confidence) or None.
    
    When multiple positions have similar match ratios, prefers the one closest to original_line.
    """
    if not context or not file_lines:
        return None
    
    # Normalize context for matching
    norm_context = [normalize_for_matching(line) for line in context]
    
    # Count empty lines in context for structural matching
    context_empty_count = sum(1 for line in context if not line.strip())
    
    best_ratio = 0.0
    best_pos = None
    context_len = len(context)
    matches = []  # Store all good matches with their empty line counts
    
    # Search entire file for best match
    for i in range(len(file_lines) - context_len + 1):
        segment = [normalize_for_matching(line.rstrip('\n\r')) for line in file_lines[i:i + context_len]]
        ratio = SequenceMatcher(None, norm_context, segment).ratio()
        
        # Track empty line count for this segment
        segment_empty_count = sum(1 for line in file_lines[i:i + context_len] if not line.strip())
        
        if ratio > 0.7:  # Require 70% match
            matches.append((i, ratio, segment_empty_count))
            if ratio > best_ratio:
                best_ratio = ratio
                best_pos = i
    
    if not matches:
        return None
    
    # Prefer matches with exact empty line count when there are multiple good matches
    if len(matches) > 1:
        threshold = best_ratio * 0.95  # Within 5% of best
        good_matches = [(pos, ratio, empty_cnt) for pos, ratio, empty_cnt in matches if ratio >= threshold]
        
        # First, try to find matches with exact empty line count
        exact_empty_matches = [(pos, ratio) for pos, ratio, empty_cnt in good_matches if empty_cnt == context_empty_count]
        
        if exact_empty_matches:
            # Among exact matches, prefer closest to original line
            if original_line is not None:
                best_pos = min(exact_empty_matches, key=lambda m: abs(m[0] - (original_line - 1)))[0]
            else:
                best_pos = exact_empty_matches[0][0]
        elif original_line is not None:
            # No exact matches, prefer closest to original line
            best_pos = min(good_matches, key=lambda m: abs(m[0] - (original_line - 1)))[0]
    
    return (best_pos, best_ratio)


def correct_hunk_line_numbers(hunks: List[Dict[str, Any]], file_lines: List[str]) -> Tuple[List[Dict[str, Any]], List[int]]:
    """
    Correct hunk line numbers by finding best context matches in the file.
    Adds 'line_number_corrected' and 'correction_confidence' metadata to corrected hunks.
    Filters out large deletions with low confidence to prevent corruption.
    Also validates function context from hunk headers to prevent applying diffs to wrong functions.
    
    Returns:
        Tuple of (corrected_hunks, skipped_hunk_numbers)
    """
    if not hunks or not file_lines:
        return hunks, []
    
    corrected = []
    corrections = 0
    skipped = 0
    skipped_hunk_numbers = []
    
    for i, hunk in enumerate(hunks, 1):
        old_start = hunk.get('old_start', 1)
        context = extract_context_from_hunk(hunk)
        
        
        if not context:
            corrected.append(hunk)
            continue
        
        # Extract and validate function context from hunk header
        header = hunk.get('header', '')
        function_context = extract_function_context_from_header(header)
        
        # Find best match, passing original line for proximity preference
        result = find_best_match_position(context, file_lines, old_start)
        
        
        if result:
            pos, confidence = result
            new_start = pos + 1  # Convert to 1-based
            
            # Validate function context if present in hunk header
            # Only enforce when fuzzy match confidence is not perfect
            # High confidence (>= 0.95) means context is distinctive enough, skip function name check
            # Lower confidence means we need function context to prevent corruption
            if function_context and confidence < 0.95:
                if not validate_function_context_at_position(function_context, file_lines, pos):
                    logger.warning(f"Hunk {i}: function context '{function_context}' not found, confidence {confidence:.2f} - skipping to prevent corruption")
                    skipped += 1
                    skipped_hunk_numbers.append(i)
                    continue
            elif function_context and confidence >= 0.95:
                logger.debug(f"Hunk {i}: skipping function context validation due to high confidence match ({confidence:.2f})")
            
            # For large deletions, require higher confidence to avoid removing wrong content
            old_count = hunk.get('old_count', 0)
            new_count = hunk.get('new_count', 0)
            is_large_deletion = (old_count - new_count) > 20
            min_confidence = 0.90 if is_large_deletion else 0.80
            
            
            # Special case: if this is a large deletion with low confidence,
            # check if there are duplicate occurrences later in the file
            if is_large_deletion and confidence < min_confidence:
                # Search for better matches further in the file
                better_matches = []
                for i in range(len(file_lines) - len(context) + 1):
                    if i == pos:  # Skip the one we already found
                        continue
                    segment = [normalize_for_matching(line.rstrip('\n\r')) for line in file_lines[i:i + len(context)]]
                    norm_context = [normalize_for_matching(line) for line in context]
                    ratio = SequenceMatcher(None, norm_context, segment).ratio()
                    if ratio >= min_confidence:
                        better_matches.append((i + 1, ratio))
                
                if better_matches:
                    # Found better match(es) - use the one closest to original line
                    new_start, confidence = min(better_matches, key=lambda m: abs(m[0] - old_start))
                    logger.info(f"Hunk {i}: found better match for large deletion at line {new_start} (confidence {confidence:.2f})")
                else:
                    # No better match found and confidence too low - skip this hunk entirely
                    logger.warning(f"Hunk {i}: large deletion has low confidence ({confidence:.2f}) and no better match found - skipping to prevent corruption")
                    skipped += 1
                    skipped_hunk_numbers.append(i)
                    logger.info(f"DEBUG: About to continue, skipped count is now {skipped}")
                    continue
            
            logger.info(f"DEBUG: After large deletion check for hunk {i}, confidence={confidence:.2f}, min={min_confidence}")
            # Correct if different and high confidence
            if new_start != old_start and confidence > min_confidence:
                new_hunk = hunk.copy()
                new_hunk['old_start'] = new_start
                new_hunk['line_number_corrected'] = True
                new_hunk['correction_confidence'] = confidence
                corrected.append(new_hunk)
                corrections += 1
                logger.info(f"Hunk {i}: corrected line {old_start} → {new_start} (confidence {confidence:.2f})")
            else:
                # Add confidence metadata without changing flow
                hunk['correction_confidence'] = confidence
                corrected.append(hunk)
        else:
            corrected.append(hunk)
    
    if corrections:
        logger.info(f"Corrected {corrections}/{len(hunks)} hunk line numbers")
    if skipped:
        logger.warning(f"Skipped {skipped}/{len(hunks)} hunks to prevent corruption")
    
    return corrected, skipped_hunk_numbers
