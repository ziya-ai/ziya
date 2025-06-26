"""
Validation utilities for diffs and patches.
"""

from typing import List, Dict, Any, Tuple
from itertools import zip_longest
import re
import logging
import difflib

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

    # Look for definitive indicators of new file creation
    has_new_file_mode = False
    has_dev_null_source = False
    
    for i, line in enumerate(diff_lines[:10]):
        # Case 1: Standard git diff new file
        if line.startswith('@@ -0,0'):
            logger.debug("Detected new file from zero hunk marker")
            return True

        # Case 2: Empty source file indicator
        if line == '--- /dev/null':
            has_dev_null_source = True
            logger.debug("Detected new file from /dev/null source")

        # Case 3: New file mode
        if 'new file mode' in line:
            has_new_file_mode = True
            logger.debug("Detected new file from mode marker")
    
    # Only consider it a new file if we have both indicators or the zero hunk marker
    if has_new_file_mode and has_dev_null_source:
        logger.debug("Confirmed new file creation: has both new file mode and /dev/null source")
        return True

    logger.debug("No new file indicators found")
    return False

    # Additional sanity checks - new files shouldn't have these characteristics
    has_delete_lines = any(line.startswith('-') and not line.startswith('---') for line in diff_lines)
    if has_delete_lines:
        logger.debug("Found delete lines - not a new file creation")
        return False
    
    # Count hunks - new files should only have one hunk
    hunk_count = sum(1 for line in diff_lines if line.startswith('@@'))
    if hunk_count > 1:
        logger.debug(f"Found {hunk_count} hunks - new files should only have one hunk")
        return False

    # Only consider it a new file if we have both indicators or the zero hunk marker
    if has_new_file_mode and has_dev_null_source:
        logger.debug("Confirmed new file creation: has both new file mode and /dev/null source")
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
    normalized = normalize_escape_sequences(normalized)
    
    # Finally strip whitespace
    return normalized.strip()
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

def is_hunk_already_applied(file_lines: List[str], hunk: Dict[str, Any], pos: int, ignore_whitespace: bool = True) -> bool:
    """
    Check if a hunk is already applied at the given position with improved handling
    for invisible Unicode characters, escape sequences, and whitespace.
    
    Args:
        file_lines: List of lines from the file
        hunk: Dictionary containing hunk information
        pos: Position to check
        ignore_whitespace: Whether to ignore whitespace differences
        
    Returns:
        True if the hunk is already applied, False otherwise
    """
    # Log type and content for debugging
    hunk_old_start = hunk.get('old_start', 'N/A') if isinstance(hunk, dict) else 'N/A'
    logger.debug(f"is_hunk_already_applied called for pos={pos}, hunk_old_start={hunk_old_start}")
    logger.debug(f"  Hunk type: {type(hunk)}, file_lines length: {len(file_lines)}")
    logger.debug(f"  Hunk content (preview): {repr(hunk)[:200]}...")
    
    # Handle edge cases
    if not hunk.get('new_lines') or pos >= len(file_lines):
        logger.debug(f"Empty hunk or position {pos} beyond file length {len(file_lines)}")
        return False
    
    # Extract the removed and added lines from the hunk
    removed_lines, added_lines = extract_diff_changes(hunk)
    new_lines = hunk.get('new_lines', [])
    
    # Validate hunk header if present
    if not _is_valid_hunk_header(hunk):
        return False
    
    # Handle no-op hunks
    if not removed_lines and not added_lines:
        logger.debug("No actual changes in hunk (no removed or added lines)")
        return True
    
    # For pure additions, check if content already exists in file
    if len(removed_lines) == 0 and len(added_lines) > 0:
        return _check_pure_addition_already_applied(file_lines, added_lines)
    
    # For hunks with removals, validate that the content to be removed matches
    if removed_lines and not _validate_removal_content(file_lines, removed_lines, pos):
        return False
    
    # Check if the expected result (new_lines) is already present at this position
    return _check_expected_content_match(file_lines, new_lines, pos, ignore_whitespace)


def _is_valid_hunk_header(hunk: Dict[str, Any]) -> bool:
    """Check if the hunk header is valid."""
    if 'header' in hunk and '@@ -' in hunk['header']:
        header_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', hunk['header'])
        if not header_match:
            logger.warning(f"Malformed hunk header: {hunk['header']}")
            return False
    return True


def _check_pure_addition_already_applied(file_lines: List[str], added_lines: List[str]) -> bool:
    """Check if a pure addition (no removals) is already applied."""
    # Check if the exact content exists anywhere in the file
    added_content = "\n".join([normalize_line_for_comparison(line) for line in added_lines])
    file_content = "\n".join([normalize_line_for_comparison(line) for line in file_lines])
    
    if added_content not in file_content:
        logger.debug("Pure addition not found in file content")
        return False
    
    # Check for duplicate declarations
    return _check_duplicate_declarations(file_lines, added_lines)


def _check_duplicate_declarations(file_lines: List[str], added_lines: List[str]) -> bool:
    """Check if added lines contain declarations that already exist in the file."""
    declaration_patterns = [
        r'const\s+\w+\s*=',  # const x =
        r'let\s+\w+\s*=',    # let x =
        r'var\s+\w+\s*=',    # var x =
        r'function\s+\w+\s*\(',  # function x(
        r'class\s+\w+\s*{',  # class x {
        r'interface\s+\w+\s*{',  # interface x {
        r'type\s+\w+\s*=',   # type x =
        r'enum\s+\w+\s*{',   # enum x {
    ]
    
    for added_line in added_lines:
        for pattern in declaration_patterns:
            match = re.search(pattern, added_line)
            if match:
                # Extract declaration name
                declaration_name = None
                for m in re.finditer(r'\b(\w+)\b', added_line[match.start():]):
                    if m.group(1) not in ['const', 'let', 'var', 'function', 'class', 'interface', 'type', 'enum']:
                        declaration_name = m.group(1)
                        break
                
                if declaration_name:
                    # Check if this declaration already exists elsewhere in the file
                    for line in file_lines:
                        if declaration_name in line:
                            for p in declaration_patterns:
                                if re.search(p + r'.*\b' + re.escape(declaration_name) + r'\b', line):
                                    logger.debug(f"Found duplicate declaration of '{declaration_name}'")
                                    return True
    return False


def _validate_removal_content(file_lines: List[str], removed_lines: List[str], pos: int) -> bool:
    """Validate that the content to be removed matches what's in the file."""
    if pos + len(removed_lines) > len(file_lines):
        return False
    
    file_slice = file_lines[pos:pos+len(removed_lines)]
    normalized_file_slice = [normalize_line_for_comparison(line) for line in file_slice]
    normalized_removed_lines = [normalize_line_for_comparison(line) for line in removed_lines]
    
    if normalized_file_slice != normalized_removed_lines:
        similarity = difflib.SequenceMatcher(None, 
                                           "\n".join(normalized_file_slice), 
                                           "\n".join(normalized_removed_lines)).ratio()
        logger.debug(f"File content doesn't match what we're trying to remove at position {pos} (similarity: {similarity:.2f})")
        logger.debug(f"File content: {normalized_file_slice}")
        logger.debug(f"Removed lines: {normalized_removed_lines}")
        return False
    
    return True


def _check_expected_content_match(file_lines: List[str], new_lines: List[str], pos: int, ignore_whitespace: bool) -> bool:
    """Check if the expected content after applying the hunk is already present."""
    if pos + len(new_lines) > len(file_lines):
        logger.debug(f"Not enough lines to compare at position {pos}")
        return False
    
    file_slice = file_lines[pos:pos+len(new_lines)]
    
    # Try exact match first
    if _lines_match_exactly(file_slice, new_lines):
        logger.debug(f"Exact match of expected content found at position {pos}")
        return True
    
    # Try with various normalizations
    if _lines_match_with_normalization(file_slice, new_lines, ignore_whitespace):
        return True
    
    # Try fuzzy matching as last resort
    return _lines_match_fuzzy(file_slice, new_lines)


def _lines_match_exactly(file_lines: List[str], expected_lines: List[str]) -> bool:
    """Check if lines match exactly."""
    for file_line, expected_line in zip(file_lines, expected_lines):
        if normalize_line_for_comparison(file_line) != normalize_line_for_comparison(expected_line):
            return False
    return True


def _lines_match_with_normalization(file_lines: List[str], expected_lines: List[str], ignore_whitespace: bool) -> bool:
    """Check if lines match with various normalizations applied."""
    # Check for whitespace-only changes
    if _check_whitespace_only_changes(file_lines, expected_lines, ignore_whitespace):
        return True
    
    # Check for invisible Unicode characters
    if _check_invisible_unicode_match(file_lines, expected_lines):
        return True
    
    # Check for escape sequences
    if _check_escape_sequence_match(file_lines, expected_lines):
        return True
    
    return False


def _check_whitespace_only_changes(file_lines: List[str], expected_lines: List[str], ignore_whitespace: bool) -> bool:
    """Check if the differences are only in whitespace."""
    if len(file_lines) != len(expected_lines):
        return False
    
    # Check if content is the same ignoring whitespace
    whitespace_only = True
    for file_line, expected_line in zip(file_lines, expected_lines):
        if normalize_line_for_comparison(file_line).strip() != normalize_line_for_comparison(expected_line).strip():
            whitespace_only = False
            break
    
    if not whitespace_only:
        return False
    
    # For whitespace-only changes, check exact match based on ignore_whitespace setting
    for file_line, expected_line in zip(file_lines, expected_lines):
        if not ignore_whitespace:
            if file_line.rstrip('\r\n') != expected_line.rstrip('\r\n'):
                # Try normalizing invisible characters
                if normalize_unicode(file_line.rstrip('\r\n')) != normalize_unicode(expected_line.rstrip('\r\n')):
                    return False
        else:
            if normalize_line_for_comparison(file_line).strip() != normalize_line_for_comparison(expected_line).strip():
                return False
    
    logger.debug("Whitespace-only changes already applied")
    return True


def _check_invisible_unicode_match(file_lines: List[str], expected_lines: List[str]) -> bool:
    """Check if lines match when invisible Unicode characters are normalized."""
    if not any('\u200B' in line or '\u200C' in line or '\u200D' in line or '\uFEFF' in line for line in expected_lines):
        return False
    
    for file_line, expected_line in zip(file_lines, expected_lines):
        normalized_file_line = normalize_unicode(file_line)
        normalized_expected_line = normalize_unicode(expected_line)
        
        if normalize_line_for_comparison(normalized_file_line) != normalize_line_for_comparison(normalized_expected_line):
            return False
    
    logger.debug("Content with invisible Unicode characters already applied (normalized)")
    return True


def _check_escape_sequence_match(file_lines: List[str], expected_lines: List[str]) -> bool:
    """Check if lines match when escape sequences are normalized."""
    if not any('\\n' in line or '\\r' in line or '\\t' in line or '\\\\' in line for line in expected_lines):
        return False
    
    for file_line, expected_line in zip(file_lines, expected_lines):
        normalized_file_line = normalize_escape_sequences(file_line)
        normalized_expected_line = normalize_escape_sequences(expected_line)
        
        if normalize_line_for_comparison(normalized_file_line) != normalize_line_for_comparison(normalized_expected_line):
            return False
    
    logger.debug("Content with escape sequences already applied (normalized)")
    return True


def _lines_match_fuzzy(file_lines: List[str], expected_lines: List[str]) -> bool:
    """Check if lines match using fuzzy matching."""
    if not expected_lines:
        return False
    
    # Normalize both sides for comparison
    normalized_file = [normalize_line_for_comparison(line) for line in file_lines]
    normalized_expected = [normalize_line_for_comparison(line) for line in expected_lines]
    
    similarity = calculate_block_similarity(normalized_file, normalized_expected)
    
    # Very high similarity suggests the changes are already applied
    if similarity >= 0.95:
        logger.debug(f"Very high similarity ({similarity:.2f}) suggests hunk already applied")
        return True
    
    return False
