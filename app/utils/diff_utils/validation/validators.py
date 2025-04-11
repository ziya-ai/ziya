"""
Validation utilities for diffs and patches.
"""

from typing import List, Dict, Any, Tuple
from itertools import zip_longest
import re
import logging

logger = logging.getLogger("ZIYA")
from ..core.utils import calculate_block_similarity, normalize_escapes
from ..core.unicode_handling import normalize_unicode
from ..core.escape_handling import normalize_escape_sequences
from ..core.text_normalization import normalize_text_for_comparison

def is_new_file_creation(diff_lines: List[str]) -> bool:
    """
    Determine if a diff represents a new file creation.
    
    Args:
        diff_lines: The lines of the diff
        
    Returns:
        True if the diff represents a new file creation, False otherwise
    """
    if not diff_lines:
        return False

    logger.debug(f"Analyzing diff lines for new file creation ({len(diff_lines)} lines):")
    for i, line in enumerate(diff_lines[:10]):
        logger.debug(f"Line {i}: {line[:100]}")  # Log first 100 chars of each line

    # Look for any indication this is a new file
    for i, line in enumerate(diff_lines[:10]):
        # Case 1: Standard git diff new file
        if line.startswith('@@ -0,0'):
            logger.debug("Detected new file from zero hunk marker")
            return True

        # Case 2: Empty source file indicator
        if line == '--- /dev/null':
            logger.debug("Detected new file from /dev/null source")
            return True

        # Case 3: New file mode
        if 'new file mode' in line:
            logger.debug("Detected new file from mode marker")
            return True

    logger.debug("No new file indicators found")
    return False

def normalize_line_for_comparison(line: str) -> str:
    """
    Normalize a line for comparison, handling whitespace, invisible characters, and escape sequences.
    
    Args:
        line: The line to normalize
        
    Returns:
        The normalized line
    """
    if not line:
        return ""
    
    # First normalize Unicode characters to handle invisible characters
    from ..core.unicode_handling import normalize_unicode
    normalized = normalize_unicode(line)
    
    # Then normalize escape sequences - preserve literals for code comparison
    from ..core.escape_handling import normalize_escape_sequences
    normalized = normalize_escape_sequences(normalized, preserve_literals=True)
    
    # Finally normalize whitespace - only trim leading/trailing
    normalized = normalized.strip()
    
    return normalized

def extract_diff_changes(hunk: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    Extract the removed and added lines from a hunk.
    
    Args:
        hunk: The hunk to extract changes from
        
    Returns:
        A tuple of (removed_lines, added_lines)
    """
    removed_lines = []
    added_lines = []
    
    # First try to extract from old_block and new_block (preferred format)
    if 'old_block' in hunk and 'new_block' in hunk:
        for line in hunk.get('old_block', []):
            if line.startswith('-'):
                removed_lines.append(line[1:])
        for line in hunk.get('new_block', []):
            if line.startswith('+'):
                added_lines.append(line[1:])
    # Fall back to lines if old_block/new_block not available
    elif 'lines' in hunk:
        for line in hunk.get('lines', []):
            if line.startswith('-'):
                removed_lines.append(line[1:])
            elif line.startswith('+'):
                added_lines.append(line[1:])
    
    return removed_lines, added_lines

def is_hunk_already_applied(file_lines: List[str], hunk: Dict[str, Any], pos: int) -> bool:
    """
    Check if a hunk is already applied at the given position with improved handling
    for invisible Unicode characters, escape sequences, and whitespace.
    
    Args:
        file_lines: List of lines from the file
        hunk: Dictionary containing hunk information
        pos: Position to check
        
    Returns:
        True if the hunk is already applied, False otherwise
    """
    # Log type and content for debugging
    hunk_old_start = hunk.get('old_start', 'N/A') if isinstance(hunk, dict) else 'N/A'
    logger.debug(f"is_hunk_already_applied called for pos={pos}, hunk_old_start={hunk_old_start}")
    logger.debug(f"  Hunk type: {type(hunk)}, file_lines length: {len(file_lines)}")
    logger.debug(f"  Hunk content (preview): {repr(hunk)[:200]}...")
    
    # Handle edge cases
    if not hunk.get('new_lines') or pos > len(file_lines):
        logger.debug(f"Empty hunk or position {pos} beyond file length {len(file_lines)}")
        return False

    # Get the lines we're working with
    window_size = max(len(hunk.get('old_block', [])), len(hunk.get('new_lines', [])))
    if pos + window_size > len(file_lines):
        window_size = len(file_lines) - pos
    available_lines = file_lines[pos:pos + window_size]
    
    # Extract the removed and added lines from the hunk
    removed_lines, added_lines = extract_diff_changes(hunk)
    
    # If there are no actual changes (no removed or added lines), it's a no-op
    if not removed_lines and not added_lines:
        logger.debug("No actual changes in hunk (no removed or added lines)")
        return True
    
    # If this is a completely new file or section, it can't be already applied
    if all(line.startswith('+') for line in hunk.get('old_block', [])):
        logger.debug("Hunk is adding completely new content, can't be already applied")
        return False
    
    # If this is a simple replacement (one line removed, one line added)
    # Check if the added line is already in the file at this position
    if len(removed_lines) == 1 and len(added_lines) == 1:
        if pos < len(file_lines):
            # Use our enhanced normalization for better comparison
            normalized_file_line = normalize_line_for_comparison(file_lines[pos])
            normalized_added_line = normalize_line_for_comparison(added_lines[0])
            normalized_removed_line = normalize_line_for_comparison(removed_lines[0])
            
            # If the file already has the added line (not the removed line)
            if normalized_file_line == normalized_added_line and normalized_file_line != normalized_removed_line:
                logger.debug(f"Found added line already in file at position {pos}")
                return True
    
    # Check if the file content at this position matches what we expect after applying the hunk
    if len(available_lines) >= len(hunk.get('new_lines', [])):
        # Extract the expected content after applying the hunk
        expected_lines = hunk.get('new_lines', [])
        
        # If there are no expected lines, this can't be applied
        if not expected_lines:
            # If it's a pure deletion, this check isn't sufficient.
            # For now, assume if new_lines is empty, it's not "already applied" in the sense of content matching.
            logger.debug("Hunk results in empty content (deletion), cannot match based on new_lines.")
            return False
        
        # Check if the available lines match the expected lines
        if len(available_lines) >= len(expected_lines):
            # Check for added lines that don't exist in the file
            # This is critical for detecting hunks that add new content
            if len(hunk.get('new_block', [])) > len(hunk.get('old_block', [])):
                # Count the number of added lines in the hunk
                added_line_count = sum(1 for line in hunk.get('new_block', []) if line.startswith('+'))
                
                # If the hunk adds lines, we need to check if those lines exist in the file
                if added_line_count > 0:
                    # Extract just the added lines from the hunk
                    added_content = [line[1:] for line in hunk.get('new_block', []) if line.startswith('+')]
                    
                    # Check if each added line exists in the available lines
                    for added_line in added_content:
                        normalized_added = normalize_line_for_comparison(added_line)
                        found = False
                        for file_line in available_lines:
                            if normalize_line_for_comparison(file_line) == normalized_added:
                                found = True
                                break
                        
                        if not found:
                            # If any added line is not found, the hunk is not applied
                            logger.debug(f"Added line not found in file: {added_line}")
                            return False
            
            # Now check for exact match of expected content
            exact_match = True
            for i, expected_line in enumerate(expected_lines):
                if i >= len(available_lines):
                    exact_match = False
                    break
                
                # Use our enhanced normalization for better comparison
                normalized_file_line = normalize_line_for_comparison(available_lines[i])
                normalized_expected_line = normalize_line_for_comparison(expected_line)
                
                if normalized_file_line != normalized_expected_line:
                    # If normalized versions don't match, it's definitely not applied
                    exact_match = False
                    break
                # else: both normalized and raw lines match for this line, continue checking next line
            
            if exact_match:
                logger.debug(f"Exact match of expected content found at position {pos}")
                return True
    
    # Check if this is a whitespace-only change
    if len(removed_lines) == len(added_lines):
        whitespace_only = True
        for removed, added in zip(removed_lines, added_lines):
            # Compare non-whitespace content
            if normalize_line_for_comparison(removed).strip() != normalize_line_for_comparison(added).strip():
                whitespace_only = False
                break
        
        if whitespace_only and removed_lines:  # Only if there are actual changes
            # For whitespace-only changes, check if the file already has the correct whitespace
            if len(available_lines) >= len(added_lines):
                all_match = True
                for i, added_line in enumerate(added_lines):
                    if i >= len(available_lines):
                        all_match = False
                        break
                    
                    # Compare with exact whitespace
                    if available_lines[i].rstrip('\r\n') != added_line.rstrip('\r\n'):
                        # Try normalizing invisible characters
                        if normalize_unicode(available_lines[i].rstrip('\r\n')) != normalize_unicode(added_line.rstrip('\r\n')):
                            all_match = False
                            break
                
                if all_match:
                    logger.debug("Whitespace-only changes already applied")
                    return True
    
    # Check for invisible Unicode characters
    if any('\u200B' in line or '\u200C' in line or '\u200D' in line or '\uFEFF' in line for line in added_lines):
        # This hunk contains invisible Unicode characters
        # Check if the file already has the content with or without the invisible characters
        if len(available_lines) >= len(added_lines):
            all_match = True
            for i, added_line in enumerate(added_lines):
                if i >= len(available_lines):
                    all_match = False
                    break
                
                # Normalize both lines to remove invisible characters
                normalized_file_line = normalize_unicode(available_lines[i])
                normalized_added_line = normalize_unicode(added_line)
                
                # Compare normalized content
                if normalize_line_for_comparison(normalized_file_line) != normalize_line_for_comparison(normalized_added_line):
                    all_match = False
                    break
            
            if all_match:
                logger.debug("Content with invisible Unicode characters already applied (normalized)")
                return True
    
    # Check for escape sequences
    if any('\\n' in line or '\\r' in line or '\\t' in line or '\\\\' in line for line in added_lines):
        # This hunk contains escape sequences
        # Check if the file already has the content with properly handled escape sequences
        if len(available_lines) >= len(added_lines):
            all_match = True
            for i, added_line in enumerate(added_lines):
                if i >= len(available_lines):
                    all_match = False
                    break
                
                # Normalize both lines to handle escape sequences
                normalized_file_line = normalize_escape_sequences(available_lines[i])
                normalized_added_line = normalize_escape_sequences(added_line)
                
                # Compare normalized content
                if normalize_line_for_comparison(normalized_file_line) != normalize_line_for_comparison(normalized_added_line):
                    all_match = False
                    break
            
            if all_match:
                logger.debug("Content with escape sequences already applied (normalized)")
                return True
    
    # Calculate overall similarity for fuzzy matching
    if len(available_lines) >= len(added_lines) and added_lines:
        # Normalize both sides for comparison
        normalized_available = [normalize_line_for_comparison(line) for line in available_lines[:len(added_lines)]]
        normalized_added = [normalize_line_for_comparison(line) for line in added_lines]
        
        similarity = calculate_block_similarity(normalized_available, normalized_added)
        
        # Very high similarity suggests the changes are already applied
        if similarity >= 0.95:
            logger.debug(f"Very high similarity ({similarity:.2f}) suggests hunk already applied")
            return True
    
    return False
