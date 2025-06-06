"""
Enhanced patch application utilities with improved fuzzy matching.

This module extends the core patch application functions with enhanced fuzzy matching
to improve the success rate of diff application.
"""

from typing import List, Optional, Tuple, Dict, Any
import logging
import re

from ..core.exceptions import PatchApplicationError
from ..core.config import get_max_offset, get_confidence_threshold
from ..parsing.diff_parser import parse_unified_diff_exact_plus
from ..validation.validators import normalize_line_for_comparison, is_hunk_already_applied
from ..validation.duplicate_detector import verify_no_duplicates
from .enhanced_fuzzy_match import find_best_chunk_position_enhanced, find_best_chunk_position_with_fallbacks

# Configure logging
logger = logging.getLogger(__name__)

def apply_diff_with_enhanced_matching(
    file_path: str, 
    diff_content: str, 
    original_lines_with_endings: List[str], 
    skip_hunks: List[int] = None
) -> List[str]:
    """
    Apply a diff using enhanced fuzzy matching.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply 
        original_lines_with_endings: The original file content as a list of lines,
          preserving original line endings.
        skip_hunks: Optional list of hunk IDs to skip (already applied)
    
    Returns:
        The modified file content as a list of lines, preserving original line endings.
    """
    logger.info(f"Applying diff to {file_path} using enhanced fuzzy matching")
    
    # Initialize skip_hunks if not provided
    if skip_hunks is None:
        skip_hunks = []
    
    if skip_hunks:
        logger.info(f"Skipping already applied hunks: {skip_hunks}")
    
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
    
    logger.debug(f"Parsed {len(hunks)} hunks for enhanced matching")
    hunk_failures = []
    final_lines_with_endings = original_lines_with_endings.copy()
    offset = 0
    
    # Sort hunks by old_start
    hunks.sort(key=lambda h: h['old_start'])
    
    # Track applied hunks for better line number adjustment
    applied_hunks = []
    
    for hunk_idx, h in enumerate(hunks, start=1):
        # Skip hunks that are in the skip_hunks list
        if h.get('number') in skip_hunks:
            logger.info(f"Skipping hunk #{hunk_idx} (ID #{h.get('number')}) as it's in the skip list")
            continue
        
        logger.debug(f"Processing hunk #{hunk_idx} with offset {offset}")
        
        # --- Calculate initial position ---
        old_start_0based = h['old_start'] - 1
        
        # For multi-hunk diffs, adjust position based on previously applied hunks
        if applied_hunks:
            adjusted_pos = old_start_0based
            
            for prev_h, prev_pos, prev_removed, prev_added in applied_hunks:
                if h['old_start'] > prev_h['old_start']:
                    if old_start_0based >= prev_h['old_start'] + prev_h['old_count'] - 1:
                        adjusted_pos += (prev_added - prev_removed)
            
            initial_pos = max(0, min(adjusted_pos, len(final_lines_with_endings)))
            logger.debug(f"Hunk #{hunk_idx}: Multi-hunk adjusted position={initial_pos} (original={old_start_0based})")
        else:
            initial_pos = max(0, min(old_start_0based + offset, len(final_lines_with_endings)))
            logger.debug(f"Hunk #{hunk_idx}: Adjusted initial_pos={initial_pos} (original={old_start_0based}, offset={offset})")
        
        # --- Try strict match first ---
        strict_ok = False
        strict_checked_pos = initial_pos
        old_block_lines = h['old_block']
        actual_old_block_count = len(old_block_lines)
        
        if strict_checked_pos + actual_old_block_count <= len(final_lines_with_endings):
            file_slice = final_lines_with_endings[strict_checked_pos : strict_checked_pos + actual_old_block_count]
            if old_block_lines:
                normalized_file_slice = [normalize_line_for_comparison(line) for line in file_slice]
                normalized_old_block = [normalize_line_for_comparison(line) for line in old_block_lines]
                if normalized_file_slice == normalized_old_block:
                    strict_ok = True
                    logger.debug(f"Hunk #{hunk_idx}: strict match at pos={strict_checked_pos}")
        
        remove_pos = -1  # Initialize remove_pos
        
        if strict_ok:
            remove_pos = strict_checked_pos
            logger.debug(f"Hunk #{hunk_idx}: Using strict match position {remove_pos}")
        else:
            # --- Try enhanced fuzzy matching ---
            logger.debug(f"Hunk #{hunk_idx}: Attempting enhanced fuzzy matching near line {initial_pos}")
            
            # Use the enhanced fuzzy matching algorithm with comment awareness
            best_pos, best_ratio, match_details = find_best_chunk_position_with_fallbacks(
                final_lines_with_endings, h['old_block'], initial_pos
            )
            
            logger.debug(f"Hunk #{hunk_idx}: Enhanced matching result: pos={best_pos}, ratio={best_ratio:.4f}")
            logger.debug(f"Hunk #{hunk_idx}: Match details: {match_details}")
            
            # Try comment-aware matching as a last resort if regular matching failed
            if best_pos is None:
                from ..application.comment_handler import handle_comment_only_changes
                comment_pos, comment_ratio = handle_comment_only_changes(
                    file_path, final_lines_with_endings, h['old_block'], initial_pos
                )
                
                if comment_pos is not None and comment_ratio > best_ratio:
                    logger.info(f"Hunk #{hunk_idx}: Found match with comment-aware handler at pos={comment_pos} with ratio={comment_ratio:.4f}")
                    best_pos = comment_pos
                    best_ratio = comment_ratio
                    
                # Also try whitespace-aware matching
                from ..application.whitespace_handler import handle_whitespace_only_changes
                ws_pos, ws_ratio = handle_whitespace_only_changes(
                    final_lines_with_endings, h['old_block'], initial_pos
                )
                
                if ws_pos is not None and ws_ratio > best_ratio:
                    logger.info(f"Hunk #{hunk_idx}: Found match with whitespace-aware handler at pos={ws_pos} with ratio={ws_ratio:.4f}")
                    best_pos = ws_pos
                    best_ratio = ws_ratio
            
            if best_pos is not None:
                offset_diff = abs(best_pos - initial_pos)
                max_allowed_offset = get_max_offset()
                
                if offset_diff > max_allowed_offset:
                    msg = f"Hunk #{hunk_idx} => large offset ({offset_diff} > {max_allowed_offset}) found at pos {best_pos + 1}, skipping."
                    logger.error(msg)
                    hunk_failures.append((msg, {
                        "hunk": hunk_idx,
                        "offset": offset_diff,
                        "type": "large_offset",
                        "confidence": best_ratio,
                        "position": best_pos
                    }))
                    continue
                else:
                    # Verify the match
                    slice_start = max(0, best_pos)
                    slice_end = min(len(final_lines_with_endings), slice_start + len(h['old_block']))
                    file_slice = final_lines_with_endings[slice_start:slice_end]
                    
                    # Use a more relaxed verification for enhanced matching
                    match_quality = calculate_match_quality(file_slice, h['old_block'])
                    logger.debug(f"Hunk #{hunk_idx}: Match quality at pos={best_pos}: {match_quality:.4f}")
                    
                    if match_quality >= 0.7:  # 70% of lines match
                        remove_pos = best_pos
                        logger.debug(f"Hunk #{hunk_idx}: Using enhanced match position {remove_pos} with quality {match_quality:.4f}")
                    else:
                        logger.error(f"Hunk #{hunk_idx}: Enhanced match verification failed at {best_pos}")
                        hunk_failures.append((f"Enhanced match verification failed for Hunk #{hunk_idx}", {
                            "hunk": hunk_idx,
                            "type": "verification_failed",
                            "confidence": best_ratio,
                            "match_quality": match_quality,
                            "position": best_pos
                        }))
                        continue
            else:
                # Enhanced matching failed
                msg = f"Hunk #{hunk_idx} => enhanced matching failed near {initial_pos}, skipping."
                logger.error(msg)
                hunk_failures.append((msg, {
                    "hunk": hunk_idx,
                    "type": "matching_failed",
                    "confidence": best_ratio
                }))
                continue
        
        # --- Apply Hunk ---
        if remove_pos == -1:
            logger.error(f"Hunk #{hunk_idx}: Failed to determine a valid application position. Skipping.")
            hunk_failures.append(("Position undetermined", {
                "hunk": hunk_idx,
                "type": "position_undetermined"
            }))
            continue
        
        new_lines_content = h['new_lines']
        new_lines_with_endings = []
        for line in new_lines_content:
            new_lines_with_endings.append(line + dominant_ending)
        
        actual_remove_count = len(h['old_block'])
        end_remove_pos = min(remove_pos + actual_remove_count, len(final_lines_with_endings))
        
        logger.debug(f"Hunk #{hunk_idx}: Applying change at pos={remove_pos}. Removing {actual_remove_count} lines (from {remove_pos} to {end_remove_pos}). Inserting {len(new_lines_with_endings)} lines.")
        
        # --- Duplication Safety Check ---
        preview_lines = final_lines_with_endings.copy()
        preview_lines[remove_pos:end_remove_pos] = new_lines_with_endings
        preview_content = ''.join(preview_lines)
        original_content = ''.join(final_lines_with_endings)
        
        is_safe, duplicate_details = verify_no_duplicates(original_content, preview_content, remove_pos)
        if not is_safe:
            logger.warning(f"Hunk #{hunk_idx}: Detected unexpected duplicates that would be created by applying this hunk")
            hunk_failures.append((f"Unexpected duplicates detected for Hunk #{hunk_idx}", {
                "hunk": hunk_idx,
                "type": "unexpected_duplicates",
                "position": remove_pos,
                "duplicate_details": duplicate_details
            }))
            continue
        
        # --- Apply the hunk ---
        final_lines_with_endings[remove_pos:end_remove_pos] = new_lines_with_endings
        
        # --- Update Offset ---
        actual_lines_removed = end_remove_pos - remove_pos
        net_change = len(new_lines_with_endings) - actual_lines_removed
        offset += net_change
        
        # Track this hunk application
        applied_hunks.append((h, remove_pos, actual_lines_removed, len(new_lines_with_endings)))
        
        logger.debug(f"Hunk #{hunk_idx}: Applied. Lines removed: {actual_lines_removed}, lines added: {len(new_lines_with_endings)}, net change: {net_change}, new offset: {offset}")
    
    # --- Final Newline Adjustment ---
    final_content_str = "".join(final_lines_with_endings)
    normalized_content_str = final_content_str.replace('\r\n', '\n').replace('\r', '\n')
    
    last_hunk = hunks[-1] if hunks else None
    diff_likely_added_final_line = False
    if last_hunk:
        last_diff_line = diff_content.splitlines()[-1] if diff_content.splitlines() else ""
        if last_diff_line.startswith('+'):
            diff_likely_added_final_line = True
    
    should_have_final_newline = original_had_final_newline or diff_likely_added_final_line
    
    if last_hunk and last_hunk.get('missing_newline'):
        logger.debug("Last hunk has missing newline marker, ensuring no final newline")
        should_have_final_newline = False
    
    if normalized_content_str:
        normalized_content_str = normalized_content_str.rstrip()
        if should_have_final_newline:
            normalized_content_str += '\n'
    
    final_lines_normalized = normalized_content_str.splitlines(True)
    
    # Check if any hunks failed
    if hunk_failures:
        logger.error(f"Failed to apply {len(hunk_failures)} hunks.")
        
        # Determine if any changes were made
        status_type = "error"
        if offset != 0 or len(final_lines_with_endings) != len(original_lines_with_endings):
            status_type = "partial"
        
        # Collect detailed failure information
        failures = []
        for msg, details in hunk_failures:
            failures.append({
                "message": msg,
                "details": details
            })
        
        raise PatchApplicationError(
            "Some hunks failed to apply during enhanced matching",
            {
                "status": status_type,
                "failures": failures
            }
        )
    
    logger.info(f"Successfully applied {len(hunks) - len(skip_hunks)} hunks using enhanced matching for {file_path}")
    return final_lines_with_endings

def calculate_match_quality(file_slice: List[str], chunk_lines: List[str]) -> float:
    """
    Calculate the quality of a match between file_slice and chunk_lines.
    
    Args:
        file_slice: A slice of the file content
        chunk_lines: The chunk to compare against
        
    Returns:
        A quality score between 0.0 and 1.0
    """
    if not chunk_lines:
        return 1.0
    
    # Count matching lines (ignoring whitespace)
    match_count = 0
    for i, chunk_line in enumerate(chunk_lines):
        if i < len(file_slice):
            # Compare the lines ignoring whitespace
            if file_slice[i].strip() == chunk_line.strip():
                match_count += 1
            else:
                # Try a more relaxed comparison
                file_tokens = set(re.findall(r'\w+', file_slice[i]))
                chunk_tokens = set(re.findall(r'\w+', chunk_line))
                
                if file_tokens and chunk_tokens:
                    # Calculate Jaccard similarity
                    intersection = len(file_tokens.intersection(chunk_tokens))
                    union = len(file_tokens.union(chunk_tokens))
                    
                    if intersection / union >= 0.7:  # 70% token overlap
                        match_count += 0.7  # Partial match
    
    return match_count / len(chunk_lines)

def apply_diff_with_enhanced_matching_wrapper(file_path: str, diff_content: str, skip_hunks: List[int] = None) -> str:
    """
    Wrapper function for apply_diff_with_enhanced_matching that returns a string.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        skip_hunks: Optional list of hunk IDs to skip (already applied)
        
    Returns:
        The modified file content as a string
    """
    logger.info(f"Applying diff to {file_path} using enhanced matching wrapper")
    
    # Read the file content
    with open(file_path, 'r', encoding='utf-8') as f:
        original_content = f.read()
        original_lines = original_content.splitlines(True)  # Keep line endings
    
    # Apply the diff
    modified_lines = apply_diff_with_enhanced_matching(file_path, diff_content, original_lines, skip_hunks)
    return ''.join(modified_lines)
