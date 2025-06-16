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
    
    # CRITICAL FIX: For hunks that add new content (like imports), we need to be more strict
    # If this is a pure addition (no lines removed), check if the exact content exists
    if len(removed_lines) == 0 and len(added_lines) > 0:
        # This is a pure addition - be more strict about considering it already applied
        # Check if the exact content exists anywhere in the file
        added_content = "\n".join([normalize_line_for_comparison(line) for line in added_lines])
        file_content = "\n".join([normalize_line_for_comparison(line) for line in file_lines])
        
        # If the exact added content doesn't exist in the file, it's not already applied
        if added_content not in file_content:
            logger.debug(f"Pure addition not found in file content")
            return False
    
    # Check if the file content at this position matches what we're trying to remove
    # This is essential to prevent marking a hunk as "already applied" when the file content doesn't match
    # what we're trying to remove
    if removed_lines and pos + len(removed_lines) <= len(file_lines):
        file_slice_for_removed = file_lines[pos:pos+len(removed_lines)]
        
        # Normalize both for comparison
        normalized_file_slice = [normalize_line_for_comparison(line) for line in file_slice_for_removed]
        normalized_removed_lines = [normalize_line_for_comparison(line) for line in removed_lines]
        
        # If the file content doesn't match what we're trying to remove,
        # then this hunk can't be already applied here
        if normalized_file_slice != normalized_removed_lines:
            # Calculate similarity to help with debugging
            similarity = difflib.SequenceMatcher(None, 
                                               "\n".join(normalized_file_slice), 
                                               "\n".join(normalized_removed_lines)).ratio()
            logger.debug(f"File content doesn't match what we're trying to remove at position {pos} (similarity: {similarity:.2f})")
            logger.debug(f"File content: {normalized_file_slice}")
            logger.debug(f"Removed lines: {normalized_removed_lines}")
            return False
    
    # CRITICAL FIX: Direct check if the expected content after applying the hunk is already present
    # This is the most reliable way to determine if a hunk is already applied
    new_lines = hunk.get('new_lines', [])
    if pos + len(new_lines) <= len(file_lines):
        file_slice = file_lines[pos:pos+len(new_lines)]
        
        # Compare the file content with the expected content
        exact_match = True
        for i, (file_line, new_line) in enumerate(zip(file_slice, new_lines)):
            if normalize_line_for_comparison(file_line) != normalize_line_for_comparison(new_line):
                exact_match = False
                logger.debug(f"Line mismatch at position {pos+i}")
                logger.debug(f"  File: {repr(file_line)}")
                logger.debug(f"  Expected: {repr(new_line)}")
                break
        
        if exact_match:
            logger.debug(f"Exact match of expected content found at position {pos}")
            return True
    
    # Check if we have enough lines to compare
    if pos + len(new_lines) > len(file_lines):
        logger.debug(f"Not enough lines to compare at position {pos}")
        return False
    
    # Extract the file content at the position
    file_slice = file_lines[pos:pos+len(new_lines)]
    
    # Compare the file content with the expected content
    for i, (file_line, new_line) in enumerate(zip(file_slice, new_lines)):
        if normalize_line_for_comparison(file_line) != normalize_line_for_comparison(new_line):
            logger.debug(f"Line mismatch at position {pos+i}")
            logger.debug(f"  File: {repr(file_line)}")
            logger.debug(f"  Expected: {repr(new_line)}")
            return False
    
    logger.debug(f"Hunk already applied at position {pos}")
    return True
    
    # ENHANCED VERIFICATION: Perform more strict checking for already applied hunks
    
    # 1. First check if the file content at this position matches what we're trying to remove
    # This is essential to prevent marking a hunk as "already applied" when the file content doesn't match
    # what we're trying to remove
    if removed_lines and pos + len(removed_lines) <= len(file_lines):
        file_slice_for_removed = file_lines[pos:pos+len(removed_lines)]
        
        # Normalize both for comparison
        normalized_file_slice = [normalize_line_for_comparison(line) for line in file_slice_for_removed]
        normalized_removed_lines = [normalize_line_for_comparison(line) for line in removed_lines]
        
        # If the file content doesn't match what we're trying to remove,
        # then this hunk can't be already applied here
        if normalized_file_slice != normalized_removed_lines:
            # Calculate similarity to help with debugging
            similarity = difflib.SequenceMatcher(None, 
                                               "\n".join(normalized_file_slice), 
                                               "\n".join(normalized_removed_lines)).ratio()
            logger.debug(f"File content doesn't match what we're trying to remove at position {pos} (similarity: {similarity:.2f})")
            logger.debug(f"File content: {normalized_file_slice}")
            logger.debug(f"Removed lines: {normalized_removed_lines}")
            return False
        
    # 2. Check if the diff header is malformed
    if 'header' in hunk and '@@ -' in hunk['header']:
        # Check if the header has proper line numbers
        header_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', hunk['header'])
        if not header_match:
            logger.warning(f"Malformed hunk header: {hunk['header']}")
            # Don't mark hunks with malformed headers as already applied
            return False
    
    # 3. If there are no actual changes (no removed or added lines), it's a no-op
    if not removed_lines and not added_lines:
        logger.debug("No actual changes in hunk (no removed or added lines)")
        return True
    
    # 4. If this is a completely new file or section, it can't be already applied
    if all(line.startswith('+') for line in hunk.get('old_block', [])):
        logger.debug("Hunk is adding completely new content, can't be already applied")
        return False
    
    # 5. Check if the file content at this position matches what we expect after applying the hunk
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
            
            if exact_match:
                logger.debug(f"Exact match of expected content found at position {pos}")
                return True
    
    # CRITICAL FIX: Check for duplicate declarations
    # This is a language-agnostic approach that looks for patterns like duplicate variable declarations
    if added_lines:
        # Look for patterns that might indicate declarations
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
        
        # Check if any added line matches a declaration pattern
        for added_line in added_lines:
            for pattern in declaration_patterns:
                match = re.search(pattern, added_line)
                if match:
                    # Found a potential declaration, check if it already exists elsewhere in the file
                    declaration_name = None
                    for m in re.finditer(r'\b(\w+)\b', added_line[match.start():]):
                        if m.group(1) not in ['const', 'let', 'var', 'function', 'class', 'interface', 'type', 'enum']:
                            declaration_name = m.group(1)
                            break
                    
                    if declaration_name:
                        # Check if this declaration already exists elsewhere in the file
                        for i, line in enumerate(file_lines):
                            if i != pos and declaration_name in line:
                                for p in declaration_patterns:
                                    if re.search(p + r'.*\b' + re.escape(declaration_name) + r'\b', line):
                                        logger.debug(f"Found duplicate declaration of '{declaration_name}' at line {i}")
                                        # This declaration already exists elsewhere, so this hunk might be already applied
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
                    
                    # Compare with exact whitespace if not ignoring whitespace
                    if not ignore_whitespace:
                        if available_lines[i].rstrip('\r\n') != added_line.rstrip('\r\n'):
                            # Try normalizing invisible characters
                            if normalize_unicode(available_lines[i].rstrip('\r\n')) != normalize_unicode(added_line.rstrip('\r\n')):
                                all_match = False
                                break
                    else:
                        # Compare ignoring whitespace
                        if normalize_line_for_comparison(available_lines[i]).strip() != normalize_line_for_comparison(added_line).strip():
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
