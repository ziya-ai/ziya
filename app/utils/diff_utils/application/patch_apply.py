from typing import List, Optional, Tuple
import re
import logging
from ..core.exceptions import PatchApplicationError
from ..core.config import get_max_offset, get_confidence_threshold
from ..parsing.diff_parser import parse_unified_diff_exact_plus
from ..validation.validators import normalize_line_for_comparison
from .fuzzy_match import find_best_chunk_position

# Configure logging
logger = logging.getLogger(__name__)

# Use the configuration system for confidence threshold
# This constant is kept for backward compatibility but should use get_confidence_threshold('medium')
MIN_CONFIDENCE = get_confidence_threshold('medium')  # Medium confidence threshold for fuzzy matching

def clamp(value, min_val, max_val):
    """Clamp a value between min and max values."""
    return max(min_val, min(max_val, value))

def apply_diff_with_difflib_hybrid_forced(
    file_path: str, diff_content: str, original_lines_with_endings: List[str]
) -> List[str]:
    """
    Apply a diff using difflib with special case handling and precise line ending/whitespace preservation.
    (Refactored to inline nested function logic - Corrected)

    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply 
        original_lines_with_endings: The original file content as a list of lines,
          preserving original line endings.

    Returns:
        The modified file content as a list of lines, preserving original line endings.
    """    

    logger.info(f"Applying diff to {file_path} using hybrid difflib (forced - inlined)")

    # --- Line Ending and Final Newline Detection ---
    original_content_str = "".join(original_lines_with_endings)
    crlf_count = original_content_str.count('\r\n')
    lf_count = original_content_str.count('\n') - crlf_count
    dominant_ending = '\r\n' if crlf_count >= lf_count else '\n'
    original_had_final_newline = original_content_str.endswith(('\n', '\r\n'))
    logger.debug(f"Detected dominant line ending: {repr(dominant_ending)}")
    logger.debug(f"Original file had final newline: {original_had_final_newline}")

    # Parse hunks
    hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
    if not hunks:
         logger.warning("No hunks parsed from diff content.")
         return original_lines_with_endings
    logger.debug(f"Parsed {len(hunks)} hunks for difflib")
    hunk_failures = []
    final_lines_with_endings = original_lines_with_endings.copy()
    offset = 0

    # Sort hunks by old_start
    # This ensures we process hunks in order of their appearance in the original file
    hunks.sort(key=lambda h: h['old_start'])
    
    # Track applied hunks for better line number adjustment
    applied_hunks = []

    for hunk_idx, h in enumerate(hunks, start=1):
        logger.debug(f"Processing hunk #{hunk_idx} with offset {offset}")
        logger.debug(f"Hunk #{hunk_idx}: Raw old_start={h['old_start']}")

        # --- Inlined calculate_initial_positions ---
        old_start_0based = h['old_start'] - 1
        old_count_header = h['old_count']
        
        # For multi-hunk diffs, we need to adjust the position based on previously applied hunks
        # This is especially important when hunks are close together or overlapping
        if applied_hunks:
            # Calculate a more accurate position based on previously applied hunks
            # This mimics how the system patch command calculates positions
            adjusted_pos = old_start_0based
            
            # Apply offsets from all previously applied hunks
            for prev_h, prev_pos, prev_removed, prev_added in applied_hunks:
                # If the current hunk is after the previous hunk in the original file
                if h['old_start'] > prev_h['old_start']:
                    # Calculate how the previous hunk affects this hunk's position
                    if old_start_0based >= prev_h['old_start'] + prev_h['old_count'] - 1:
                        # Current hunk is completely after the previous hunk
                        # Adjust by the net change in lines
                        adjusted_pos += (prev_added - prev_removed)
            
            # Use the adjusted position instead of the simple offset
            initial_pos = clamp(adjusted_pos, 0, len(final_lines_with_endings))
            logger.debug(f"Hunk #{hunk_idx}: Multi-hunk adjusted position={initial_pos} (original={old_start_0based})")
        else:
            # For the first hunk or when there are no applied hunks yet, use the simple offset
            initial_pos = clamp(old_start_0based + offset, 0, len(final_lines_with_endings))
            logger.debug(f"Hunk #{hunk_idx}: Adjusted initial_pos={initial_pos} (original={old_start_0based}, offset={offset})")
        
        available_lines = len(final_lines_with_endings) - initial_pos
        actual_old_block_count = len(h['old_block'])
        end_remove_calc = min(initial_pos + actual_old_block_count, len(final_lines_with_endings))
        logger.debug(f"Hunk #{hunk_idx}: Calculated positions - initial_pos={initial_pos}, actual_old_block_count={actual_old_block_count}, end_remove_calc={end_remove_calc}")
        # --- End Inlined ---

        remove_pos = -1 # Initialize remove_pos for this iteration

        # --- Inlined try_strict_match ---
        strict_ok = False
        strict_checked_pos = initial_pos # Position to check for strict match
        old_block_lines_strict = h['old_block']
        actual_old_block_count_strict = len(old_block_lines_strict)

        if strict_checked_pos + actual_old_block_count_strict <= len(final_lines_with_endings):
            file_slice_strict = final_lines_with_endings[strict_checked_pos : strict_checked_pos + actual_old_block_count_strict]
            if old_block_lines_strict:
                normalized_file_slice_strict = [normalize_line_for_comparison(line) for line in file_slice_strict]
                normalized_old_block_strict = [normalize_line_for_comparison(line) for line in old_block_lines_strict]
                if normalized_file_slice_strict == normalized_old_block_strict:
                    strict_ok = True
                    logger.debug(f"Hunk #{hunk_idx}: strict match at pos={strict_checked_pos}")
                else:
                    logger.debug(f"Hunk #{hunk_idx}: strict match failed at pos={strict_checked_pos}")
            elif not old_block_lines_strict: # Empty old block
                 logger.debug(f"Hunk #{hunk_idx}: old_block is empty => strict match not possible")
        # --- End Inlined ---

        if strict_ok:
            remove_pos = strict_checked_pos # Assign if strict match OK
            logger.debug(f"Hunk #{hunk_idx}: Using strict match position {remove_pos}")
        else:
            # --- Inlined try_fuzzy_match ---
            fuzzy_initial_pos_search = initial_pos # Use the initially calculated pos to search around
            fuzzy_best_ratio = 0.0
            logger.debug(f"Hunk #{hunk_idx}: Attempting fuzzy near line {fuzzy_initial_pos_search}")

            # Assuming find_best_chunk_position is imported or defined above
            normalized_final_lines_fuzzy = [normalize_line_for_comparison(line) for line in final_lines_with_endings]
            normalized_old_block_fuzzy = [normalize_line_for_comparison(line) for line in h['old_block']]
            fuzzy_best_pos, fuzzy_best_ratio = find_best_chunk_position(
                normalized_final_lines_fuzzy,                normalized_old_block_fuzzy,
                fuzzy_initial_pos_search
            )
            # --- End Inlined ---

            min_confidence = MIN_CONFIDENCE
            if fuzzy_best_pos is not None and fuzzy_best_ratio >= min_confidence:
                offset_diff = abs(fuzzy_best_pos - fuzzy_initial_pos_search)
                max_allowed_offset = get_max_offset()  # Use configurable MAX_OFFSET
                if offset_diff > max_allowed_offset:
                    msg = f"Hunk #{hunk_idx} => large offset ({offset_diff} > {max_allowed_offset}) found at pos {fuzzy_best_pos + 1}, skipping."
                    logger.error(msg)
                    hunk_failures.append((msg, {"hunk": hunk_idx, "offset": offset_diff}))
                    continue # Skip hunk
                else:
                    # --- Inlined Fuzzy Verification ---
                    logger.debug(f"Hunk #{hunk_idx}: Verifying content at fuzzy pos={fuzzy_best_pos}")
                    slice_start_fuzzy = max(0, fuzzy_best_pos)
                    slice_end_fuzzy = min(len(final_lines_with_endings), slice_start_fuzzy + len(h['old_block']))
                    fuzzy_file_slice_verify = final_lines_with_endings[slice_start_fuzzy:slice_end_fuzzy]
                    normalized_fuzzy_file_slice_verify = [normalize_line_for_comparison(line) for line in fuzzy_file_slice_verify]
                    normalized_old_block_verify = [normalize_line_for_comparison(line) for line in h['old_block']]

                    # Relaxed verification for incorrect hunk offsets
                    # If the content doesn't match exactly, try to find a better match nearby
                    if normalized_fuzzy_file_slice_verify != normalized_old_block_verify:
                        logger.warning(f"Hunk #{hunk_idx}: Initial verification failed at {fuzzy_best_pos}, trying relaxed verification")
                        
                        # Try to find a better match within a wider range
                        found_match = False
                        search_range = 30  # Search 30 lines before and after
                        
                        for offset in range(-search_range, search_range + 1):
                            test_pos = fuzzy_best_pos + offset
                            if test_pos < 0 or test_pos + len(h['old_block']) > len(final_lines_with_endings):
                                continue
                                
                            test_slice = final_lines_with_endings[test_pos:test_pos + len(h['old_block'])]
                            normalized_test_slice = [normalize_line_for_comparison(line) for line in test_slice]
                            
                            # Check if this is a better match
                            match_count = sum(1 for a, b in zip(normalized_test_slice, normalized_old_block_verify) if a == b)
                            match_ratio = match_count / len(normalized_old_block_verify) if normalized_old_block_verify else 0
                            
                            if match_ratio > 0.8:  # If 80% of lines match
                                logger.info(f"Hunk #{hunk_idx}: Found better match at position {test_pos} with ratio {match_ratio:.2f}")
                                remove_pos = test_pos
                                found_match = True
                                break
                        
                        if not found_match:
                            # LAST RESORT: If we still can't find a match but we're confident about the position,
                            # try to apply the change anyway at the fuzzy position
                            if fuzzy_best_ratio > 0.7:  # If there's at least 70% confidence
                                logger.warning(f"Hunk #{hunk_idx}: Forcing application at fuzzy position {fuzzy_best_pos} with ratio {fuzzy_best_ratio:.2f}")
                                remove_pos = fuzzy_best_pos
                                found_match = True
                            else:
                                logger.error(f"Hunk #{hunk_idx}: Fuzzy match found at {fuzzy_best_pos}, but content doesn't match old_block. Skipping.")
                                failure_info = {
                                    "status": "error",
                                    "type": "fuzzy_verification_failed",
                                    "hunk": hunk_idx,
                                    "position": fuzzy_best_pos,
                                    "confidence": fuzzy_best_ratio # Use the ratio from fuzzy match
                                }
                                hunk_failures.append((f"Fuzzy match verification failed for Hunk #{hunk_idx}", failure_info))
                                continue # Skip applying this hunk as verification failed
                    else:
                        remove_pos = fuzzy_best_pos # Assign remove_pos HERE if fuzzy OK and verified
                        logger.debug(f"Hunk #{hunk_idx}: Using fuzzy match position {remove_pos}")
                    # --- End Inlined ---
            else:
                # Fuzzy match failed or confidence too low
                msg = f"Hunk #{hunk_idx} => low confidence match (ratio={fuzzy_best_ratio:.2f}) near {fuzzy_initial_pos_search}, skipping."
                logger.error(msg)
                failure_info = {
                    "status": "error",
                    "type": "low_confidence",
                    "hunk": hunk_idx,
                    "confidence": fuzzy_best_ratio
                }
                hunk_failures.append((msg, failure_info))
                continue # Skip applying this hunk

        # --- Apply Hunk ---
        if remove_pos == -1:
             logger.error(f"Hunk #{hunk_idx}: Failed to determine a valid application position. Skipping.")
             if not any(f[1]['hunk'] == hunk_idx for f in hunk_failures):
                  hunk_failures.append(("Position undetermined", {"hunk": hunk_idx}))
             continue

        new_lines_content = h['new_lines']
        # Preserve original line endings from the file
        new_lines_with_endings = []
        for line in new_lines_content:
            # Use the dominant line ending for consistency
            new_lines_with_endings.append(line + dominant_ending)
            
        actual_remove_count = len(h['old_block']) # Use actual block length
        end_remove_pos = min(remove_pos + actual_remove_count, len(final_lines_with_endings))

        logger.debug(f"Hunk #{hunk_idx}: Applying change at pos={remove_pos}. Removing {actual_remove_count} lines (from {remove_pos} to {end_remove_pos}). Inserting {len(new_lines_with_endings)} lines.")
        logger.debug(f"Hunk #{hunk_idx}: Slice to remove: {repr(final_lines_with_endings[remove_pos:end_remove_pos])}")
        logger.debug(f"Hunk #{hunk_idx}: Lines to insert: {repr(new_lines_with_endings)}")

        final_lines_with_endings[remove_pos:end_remove_pos] = new_lines_with_endings

        # --- Update Offset ---
        # The actual number of lines removed might be different from actual_remove_count
        # if the end_remove_pos was clamped due to file length constraints
        actual_lines_removed = end_remove_pos - remove_pos
        net_change = len(new_lines_with_endings) - actual_lines_removed
        offset += net_change
        
        # Track this hunk application for future reference
        # Store the hunk, position where it was applied, lines removed, and lines added
        applied_hunks.append((h, remove_pos, actual_lines_removed, len(new_lines_with_endings)))
        
        logger.debug(f"Hunk #{hunk_idx}: Applied. Lines removed: {actual_lines_removed}, lines added: {len(new_lines_with_endings)}, net change: {net_change}, new offset: {offset}")
        
        # Important: We don't need to modify the original hunks, as we're using the offset
        # when calculating the initial_pos for each hunk. This ensures that subsequent hunks
        # are applied at the correct position, taking into account the line changes from
        # previous hunks.

    # --- Final Newline Adjustment ---
    # Convert back to a single string for easier normalization
    final_content_str = "".join(final_lines_with_endings)
 
    # 1. Normalize all line endings to LF for consistency before final check
    normalized_content_str = final_content_str.replace('\r\n', '\n').replace('\r', '\n')
 
    # 2. Handle the final newline based on original state and diff intent (heuristic)
    last_hunk = hunks[-1] if hunks else None
    diff_likely_added_final_line = False
    if last_hunk:
        last_diff_line = diff_content.splitlines()[-1] if diff_content.splitlines() else ""
        if last_diff_line.startswith('+'):
            diff_likely_added_final_line = True
 
    should_have_final_newline = original_had_final_newline or diff_likely_added_final_line
 
    # 3. Ensure correct final newline state and remove trailing blank lines
    if normalized_content_str: # Only process if not empty
        # Remove ALL trailing whitespace including newlines first
        normalized_content_str = normalized_content_str.rstrip()
        # Add back exactly one newline if it should have one
        if should_have_final_newline:
            normalized_content_str += '\n'
 
    # 4. Split back into lines (now with consistent LF endings)
    final_lines_normalized = normalized_content_str.splitlines(True) # Keep endings
 
    if hunk_failures:
        logger.error(f"Failed to apply {len(hunk_failures)} hunks.")
        # Determine overall status based on whether *any* changes were made before failure
        # This requires tracking if any hunk *was* successfully applied before a failure occurred.
        # For simplicity here, we assume if there are failures, it's at least partial if offset != 0 or list lengths differ.
        status_type = "error"
        if offset != 0 or len(final_lines_with_endings) != len(original_lines_with_endings):
             # Check if any hunk actually succeeded (this requires tracking success per hunk)
             # Simplified: assume partial if changes were made but failures occurred.
             status_type = "partial"

        raise PatchApplicationError(
            "Some hunks failed to apply during difflib stage",
            {
                "status": status_type,
                "failures": [{"message": msg, "details": details} for msg, details in hunk_failures]
            }
        )

    logger.info(f"Successfully applied {len(hunks)} hunks using difflib for {file_path}")
    return final_lines_with_endings

def apply_diff_with_difflib(file_path: str, diff_content: str) -> str:
    """
    Apply a diff using difflib.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        
    Returns:
        The modified file content as a string
    """
    logger.info(f"Applying diff to {file_path} using regular difflib")
    
    # Read the original file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
            original_lines = original_content.splitlines(True)  # Keep line endings
    except FileNotFoundError:
        original_content = ""
        original_lines = []
    
    # Apply the diff using the hybrid forced mode
    try:
        modified_lines = apply_diff_with_difflib_hybrid_forced(file_path, diff_content, original_lines)
        return ''.join(modified_lines)
    except Exception as e:
        logger.error(f"Error applying diff with hybrid forced mode: {str(e)}")
        raise
