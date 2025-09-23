"""
Utilities for detecting duplicate code in diff application.
"""
from typing import List, Tuple, Optional
import logging
import difflib

logger = logging.getLogger(__name__)

def detect_unexpected_duplicates(
    original_lines: List[str], 
    modified_lines: List[str], 
    position: int, 
    context_lines: int = 5,
    hunk_info: Optional[dict] = None
) -> Tuple[bool, Optional[dict]]:
    """
    Detect if applying a hunk would create unexpected duplicates.
    
    Args:
        original_lines: The original file content as a list of lines
        modified_lines: The modified file content after applying the hunk
        position: The position where the hunk was applied
        context_lines: Number of lines to check before and after the modification
        hunk_info: Optional hunk information to help distinguish actual changes from context
        
    Returns:
        Tuple of (has_duplicates, details)
        - has_duplicates: True if unexpected duplicates were detected
        - details: Dictionary with details about the duplication if found, None otherwise
    """
    # If the file is too short, no need to check for duplicates
    if len(original_lines) < 2 or len(modified_lines) < 2:
        return False, None
        
    # Calculate the range to examine
    start_pos = max(0, position - context_lines)
    end_pos = min(len(modified_lines), position + context_lines)
    
    # Extract the region of interest from the modified file
    region_of_interest = modified_lines[start_pos:end_pos]
    
    # Check for duplicate adjacent lines in the region
    duplicates = []
    for i in range(len(region_of_interest) - 1):
        if region_of_interest[i] == region_of_interest[i + 1]:
            # Check if this duplication existed in the original file
            orig_start_pos = max(0, start_pos - 1)  # Look a bit wider in original
            orig_end_pos = min(len(original_lines), end_pos + 1)
            orig_region = original_lines[orig_start_pos:orig_end_pos]
            
            # Count occurrences in original and modified regions (not entire file)
            orig_count = orig_region.count(region_of_interest[i])
            mod_count = region_of_interest.count(region_of_interest[i])
            
            # Only flag as duplicate if:
            # 1. There are more occurrences in the modified region than original region
            # 2. AND the line appears to be actually duplicated (not just context)
            if mod_count > orig_count:
                # Additional check: if this is just a context line that appears in both
                # the original and modified content at the same positions, it's not a real duplicate
                line_content = region_of_interest[i]
                
                # Check if this line exists in the original file at similar positions
                # If it does, it's likely just context, not a real duplication
                is_likely_context = False
                for orig_idx, orig_line in enumerate(original_lines):
                    if orig_line == line_content:
                        # If the line exists in the original at a position close to where
                        # we're seeing it in the modified content, it's likely context
                        modified_abs_pos = start_pos + i
                        if abs(orig_idx - modified_abs_pos) <= context_lines:
                            is_likely_context = True
                            break
                
                # Only report as duplicate if it's not likely just context
                if not is_likely_context:
                    duplicates.append({
                        "line": region_of_interest[i],
                        "position": start_pos + i,
                        "original_count": orig_count,
                        "modified_count": mod_count
                    })
    
    # Check for duplicate blocks (3+ lines) - but be more conservative
    for block_size in range(3, min(6, len(region_of_interest) // 3 + 1)):  # Reduced max block size and frequency
        for i in range(len(region_of_interest) - block_size * 2 + 1):
            block1 = region_of_interest[i:i+block_size]
            
            # Look for this block elsewhere in the region
            for j in range(i + block_size, len(region_of_interest) - block_size + 1):
                block2 = region_of_interest[j:j+block_size]
                
                # If blocks match, check if this duplication existed in the original
                if block1 == block2:
                    block_str = ''.join(block1)
                    
                    # Compare regions, not entire files to avoid false positives from context
                    orig_start_pos = max(0, start_pos - context_lines)
                    orig_end_pos = min(len(original_lines), end_pos + context_lines)
                    orig_region_str = ''.join(original_lines[orig_start_pos:orig_end_pos])
                    mod_region_str = ''.join(region_of_interest)
                    
                    # Count occurrences in the relevant regions
                    orig_count = orig_region_str.count(block_str)
                    mod_count = mod_region_str.count(block_str)
                    
                    # Only flag if there's a clear increase in duplications
                    if mod_count > orig_count + 1:  # Allow for one additional occurrence (the intended change)
                        duplicates.append({
                            "block": block1,
                            "positions": [start_pos + i, start_pos + j],
                            "block_size": block_size,
                            "original_count": orig_count,
                            "modified_count": mod_count
                        })
    
    # If duplicates were found, return True with details
    if duplicates:
        logger.warning(f"Detected {len(duplicates)} unexpected duplications near position {position}")
        return True, {
            "duplicates": duplicates,
            "position": position,
            "context_range": (start_pos, end_pos)
        }
    
    return False, None

def verify_no_duplicates(
    original_content: str, 
    modified_content: str, 
    position: int
) -> Tuple[bool, Optional[dict]]:
    """
    Verify that applying a change doesn't create unexpected duplicates.
    
    Args:
        original_content: The original file content as a string
        modified_content: The modified file content after applying the hunk
        position: The position where the hunk was applied
        
    Returns:
        Tuple of (is_safe, details)
        - is_safe: True if no unexpected duplicates were detected
        - details: Dictionary with details about the duplication if found, None otherwise
    """
    original_lines = original_content.splitlines(True)
    modified_lines = modified_content.splitlines(True)
    
    has_duplicates, details = detect_unexpected_duplicates(
        original_lines, modified_lines, position
    )
    
    return not has_duplicates, details
