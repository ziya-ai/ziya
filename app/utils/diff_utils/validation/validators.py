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

from functools import lru_cache

@lru_cache(maxsize=8192)
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
    
    # Strip all whitespace for fuzzy matching
    normalized = normalized.strip()
    
    return normalized
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
    
    # First try direct fields (preferred format from parse_unified_diff_exact_plus)
    if 'removed_lines' in hunk and 'added_lines' in hunk:
        removed_lines = hunk.get('removed_lines', [])
        added_lines = hunk.get('added_lines', [])
    # Try to extract from old_block and new_block
    elif 'old_block' in hunk and 'new_block' in hunk:
        for line in hunk.get('old_block', []):
            if line.startswith('-') and not line.startswith('--- '):
                removed_lines.append(line[1:])
        for line in hunk.get('new_block', []):
            if line.startswith('+') and not line.startswith('+++ '):
                added_lines.append(line[1:])
    # Fall back to lines if old_block/new_block not available
    elif 'lines' in hunk:
        for line in hunk.get('lines', []):
            if line.startswith('-') and not line.startswith('--- '):
                removed_lines.append(line[1:])
            elif line.startswith('+') and not line.startswith('+++ '):
                added_lines.append(line[1:])
    
    return removed_lines, added_lines

def detect_malformed_state(file_lines: List[str], hunk: Dict[str, Any]) -> bool:
    """
    Detect if the file is in a malformed state where the diff represents contradictory changes.
    
    Args:
        file_lines: The current file content as a list of lines
        hunk: The hunk to check
        
    Returns:
        True if malformed state is detected, False otherwise
    """
    removed_lines, added_lines = extract_diff_changes(hunk)
    
    # Debug logging
    import logging
    logger = logging.getLogger(__name__)
    logger.debug(f"Malformed check - Removed: {len(removed_lines)} lines, Added: {len(added_lines)} lines")
    if removed_lines:
        logger.debug(f"First removed line repr: {repr(removed_lines[0][:50] if removed_lines[0] else 'empty')}")
    if added_lines:
        logger.debug(f"First added line repr: {repr(added_lines[0][:50] if added_lines[0] else 'empty')}")
    logger.debug(f"added_lines is truthy: {bool(added_lines)}")
    logger.debug(f"removed_lines is truthy: {bool(removed_lines)}")
    
    # Convert to normalized strings for searching, but use exact content for whitespace-sensitive comparison
    file_content_exact = "\n".join(file_lines)
    file_content_normalized = "\n".join([normalize_line_for_comparison(line) for line in file_lines])
    
    # Check for malformed patterns:
    
    # 1. Replacement operations: trying to add existing content while removing non-existent content
    if removed_lines and added_lines:
        logger.debug("Checking replacement operation (both removed and added lines)")
        removed_content_exact = "\n".join(removed_lines)
        added_content_exact = "\n".join(added_lines)
        removed_content_normalized = "\n".join([normalize_line_for_comparison(line) for line in removed_lines])
        added_content_normalized = "\n".join([normalize_line_for_comparison(line) for line in added_lines])
        
        # CRITICAL: If one content is a substring of the other, this is a legitimate edit (e.g., removing trailing comma)
        # Check if the new content is contained within the old content or vice versa
        if removed_content_normalized in added_content_normalized or added_content_normalized in removed_content_normalized:
            logger.debug("One content is substring of the other - this is a legitimate edit, not malformed")
            return False
        
        # Check both exact and normalized content
        old_content_exists_exact = removed_content_exact in file_content_exact
        new_content_exists_exact = added_content_exact in file_content_exact
        old_content_exists_normalized = removed_content_normalized in file_content_normalized
        new_content_exists_normalized = added_content_normalized in file_content_normalized
        
        # Only flag as malformed if both old and new content exist IN PROXIMITY
        # If they exist far apart in the file, this is likely a case of incorrect line numbers
        # in the diff, not actual duplication/corruption
        if (old_content_exists_exact and new_content_exists_exact) or \
           (old_content_exists_normalized and new_content_exists_normalized):
            # Find positions of both contents
            old_pos = file_content_normalized.find(removed_content_normalized)
            new_pos = file_content_normalized.find(added_content_normalized)
            
            if old_pos >= 0 and new_pos >= 0:
                # Calculate distance in characters
                distance = abs(old_pos - new_pos)
                # Calculate expected proximity based on content size
                max_proximity = max(len(removed_content_normalized), len(added_content_normalized)) * 3
                
                # Only flag as malformed if they're close together (within 3x the content size)
                if distance > max_proximity:
                    logger.debug(f"Old and new content exist but are far apart (distance: {distance}, max: {max_proximity}) - likely incorrect line numbers, not malformed")
                    return False
        
        # Malformed pattern 1: both old and new content exist exactly in proximity (clear duplication)
        if old_content_exists_exact and new_content_exists_exact:
            return True
        
        # Malformed pattern 2: both old and new content exist in normalized form in proximity (duplication with variations)
        if old_content_exists_normalized and new_content_exists_normalized:
            # Exception: if this is a whitespace-only change, don't flag as malformed
            if removed_content_exact.replace('\t', '    ').replace(' ', '') == added_content_exact.replace('\t', '    ').replace(' ', ''):
                return False  # This is a legitimate whitespace change
            
            # Exception: if the new content is a subset of the old content being removed, this is likely a legitimate simplification
            # Check if all added lines are substrings of the removed content
            if all(normalize_line_for_comparison(added_line) in removed_content_normalized for added_line in added_lines):
                return False  # This is likely a legitimate simplification (e.g., "return a + b" -> "return a")
            
            # Exception: if this is a net deletion (removing more lines than adding) and the added content
            # exists in the file, this is likely a legitimate deletion where the "added" lines are just
            # the remaining context that was already there
            if len(removed_lines) > len(added_lines):
                return False  # This is a legitimate deletion hunk
            
            return True
        
        # Malformed pattern 3: new content exists but old doesn't (trying to add existing content)
        # Only flag as malformed if the new content exists NEAR the expected hunk location
        # If it exists far away, it's likely just a common pattern (like a variable name)
        if new_content_exists_normalized and not old_content_exists_normalized:
            # Be more lenient for very short changes (≤2 lines) - likely common patterns
            if len(added_lines) <= 2 and len(removed_lines) <= 2:
                return False  # Don't flag short changes as malformed
            
            # Check proximity: if new content exists far from the hunk location, it's not malformed
            new_pos = file_content_normalized.find(added_content_normalized)
            if new_pos >= 0:
                # Estimate hunk position in normalized content
                # Use the hunk's old_start line number as a rough guide
                old_start = hunk.get('old_start', 0)
                # Convert line number to character position (rough estimate)
                estimated_pos = sum(len(line) + 1 for line in file_lines[:old_start-1]) if old_start > 0 else 0
                distance = abs(new_pos - estimated_pos)
                max_proximity = len(added_content_normalized) * 10  # More lenient than pattern 1/2
                
                if distance > max_proximity:
                    logger.debug(f"New content exists but far from hunk location (distance: {distance}, max: {max_proximity}) - likely common pattern, not malformed")
                    return False
            
            return True
    
    # 2. Pure removals: Don't flag as malformed - let the difflib stage handle it
    # Pure deletions are not malformed even if the content doesn't exist exactly
    elif removed_lines and not added_lines:
        logger.debug("Pure removal detected - not flagging as malformed")
        return False
    
    logger.debug("Returning False - no malformed patterns detected")
    return False


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
    # Handle edge cases
    if not hunk.get('new_lines') or pos >= len(file_lines):
        return False
    
    # CRITICAL: Check for malformed state first - if detected, never mark as already applied
    if detect_malformed_state(file_lines, hunk):
        return False
    
    # Extract the removed and added lines from the hunk
    removed_lines, added_lines = extract_diff_changes(hunk)
    new_lines = hunk.get('new_lines', [])
    
    # Validate hunk header if present
    if not _is_valid_hunk_header(hunk):
        return False
    
    # Handle no-op hunks
    if not removed_lines and not added_lines:
        return True
    
    # For pure additions, check if content already exists at the expected position
    if len(removed_lines) == 0 and len(added_lines) > 0:
        return _check_pure_addition_already_applied(file_lines, added_lines, hunk, pos)
    
    # CRITICAL: For hunks with removals, validate that the content to be removed matches
    # If removal validation fails, check if it's because the hunk was already applied
    if removed_lines:
        removal_matches = _validate_removal_content(file_lines, removed_lines, pos)
        if not removal_matches:
            # Removal content doesn't match at pos
            # For modifications (has additions), check if the new content is already there
            if added_lines and _check_expected_content_match(file_lines, new_lines, pos, ignore_whitespace):
                # CRITICAL: Before marking as "already applied", verify the removal content
                # is actually gone. For multi-line distinctive blocks, check if a significant
                # portion still exists in the file - if so, we're likely matching against
                # duplicate code, not a previously-applied hunk.
                if len(removed_lines) >= 3:
                    # Check if a distinctive portion of removal content exists in file
                    # Use middle lines (skip first/last which are often generic like braces)
                    mid_start = len(removed_lines) // 4
                    mid_end = len(removed_lines) - mid_start
                    distinctive_lines = removed_lines[mid_start:mid_end]
                    
                    if len(distinctive_lines) >= 2:
                        # Check if these distinctive lines exist consecutively in the file
                        file_content = "\n".join(file_lines)
                        distinctive_content = "\n".join(distinctive_lines)
                        if distinctive_content in file_content:
                            logger.debug(f"Removal content still exists in file (checked {len(distinctive_lines)} distinctive lines) - NOT already applied")
                            return False
                
                logger.debug(f"Hunk appears already applied: removal content gone, new content present at {pos}")
                return True
            # For pure deletions or when new content doesn't match - not applied
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


def _check_pure_addition_already_applied(file_lines: List[str], added_lines: List[str], hunk: Dict[str, Any], pos: int) -> bool:
    """Check if a pure addition (no removals) is already applied with context validation."""
    
    logger.debug(f"Checking pure addition at pos {pos} - added_lines: {added_lines}")
    
    if not added_lines:
        return True
    
    # Strip trailing empty strings from added_lines (parser artifact)
    while added_lines and added_lines[-1] == '':
        added_lines = added_lines[:-1]
    
    if not added_lines:
        return True
    
    # Get context lines from the hunk
    hunk_lines = hunk.get('lines', [])
    context_lines = [line[1:] for line in hunk_lines if line.startswith(' ')]
    
    if not context_lines:
        logger.debug("No context lines in hunk - checking if added content exists anywhere in file")
        # For pure additions with no context, check if the exact content exists anywhere
        added_block = [normalize_line_for_comparison(line) for line in added_lines]
        
        # Search through the file for a matching block
        for search_pos in range(len(file_lines) - len(added_lines) + 1):
            file_block = [normalize_line_for_comparison(file_lines[search_pos + i]) 
                         for i in range(len(added_lines))]
            if file_block == added_block:
                logger.debug(f"Found added content at position {search_pos} (no context match)")
                return True
        
        logger.debug("Added content not found anywhere in file")
        return False
    
    added_block = [normalize_line_for_comparison(line) for line in added_lines]
    context_normalized = [normalize_line_for_comparison(line) for line in context_lines]
    
    # Search for context lines in the file (they may have shifted due to other hunks)
    for search_pos in range(len(file_lines) - len(context_lines) + 1):
        file_context = [normalize_line_for_comparison(file_lines[search_pos + i]) 
                       for i in range(len(context_lines))]
        
        if file_context == context_normalized:
            # Found matching context, check if added lines follow
            check_pos = search_pos + len(context_lines)
            
            if check_pos + len(added_lines) > len(file_lines):
                continue
            
            file_block = [normalize_line_for_comparison(file_lines[check_pos + i]) 
                         for i in range(len(added_lines))]
            
            if file_block == added_block:
                logger.debug(f"Pure addition already applied: context at {search_pos}, additions at {check_pos}")
                return True
    
    logger.debug("Pure addition not found with matching context")
    return False


def _check_duplicate_declarations(file_lines: List[str], added_lines: List[str]) -> bool:
    """Check if added lines contain declarations that already exist in the file."""

    for added_line in added_lines:
        # Handle import statements specifically
        if _is_import_statement(added_line):
            if _is_import_already_present(file_lines, added_line):
                logger.debug(f"Found duplicate import: {added_line.strip()}")
                return True
        else:
            # Handle other declarations
            if _is_other_declaration_duplicate(file_lines, added_line):
                return True
    return False


def _is_import_statement(line: str) -> bool:
    """Check if a line is an import statement."""
    normalized = line.strip()
    return (normalized.startswith('import ') or 
            normalized.startswith('from ') or
            normalized.startswith('const ') and ' = require(' in normalized)


def _is_import_already_present(file_lines: List[str], import_line: str) -> bool:
    """Check if an import statement is already present in the file."""
    # Extract the imported module/items from the import line
    import_line = import_line.strip()
    logger.debug(f"Checking if import already present: {repr(import_line)}")
    
    # Handle ES6 imports: import {X, Y} from 'module'
    es6_match = re.match(r'import\s+(?:\{([^}]+)\}|\*\s+as\s+\w+|\w+)\s+from\s+[\'"]([^\'"]+)[\'"]', import_line)
    if es6_match:
        imported_items = es6_match.group(1)
        module_name = es6_match.group(2)
        logger.debug(f"ES6 import detected - items: {imported_items}, module: {module_name}")
        
        # Check if the exact same import already exists
        for i, line in enumerate(file_lines):
            line = line.strip()
            if line == import_line:
                logger.debug(f"Found exact import match at line {i}: {repr(line)}")
                return True
            
            # Check if importing from the same module
            existing_match = re.match(r'import\s+(?:\{([^}]+)\}|\*\s+as\s+\w+|\w+)\s+from\s+[\'"]([^\'"]+)[\'"]', line)
            if existing_match and existing_match.group(2) == module_name:
                # If importing from same module, check if the specific items are already imported
                if imported_items:
                    existing_items = existing_match.group(1)
                    if existing_items and imported_items in existing_items:
                        logger.debug(f"Found import from same module with overlapping items at line {i}: {repr(line)}")
                        return True
    
    # Handle CommonJS imports: const X = require('module')
    cjs_match = re.match(r'const\s+(\w+)\s*=\s*require\([\'"]([^\'"]+)[\'"]\)', import_line)
    if cjs_match:
        var_name = cjs_match.group(1)
        module_name = cjs_match.group(2)
        logger.debug(f"CommonJS import detected - var: {var_name}, module: {module_name}")
        
        for i, line in enumerate(file_lines):
            line = line.strip()
            if line == import_line:
                logger.debug(f"Found exact CommonJS import match at line {i}: {repr(line)}")
                return True
            # Check for same variable name and module
            existing_match = re.match(r'const\s+(\w+)\s*=\s*require\([\'"]([^\'"]+)[\'"]\)', line)
            if existing_match and existing_match.group(1) == var_name and existing_match.group(2) == module_name:
                logger.debug(f"Found CommonJS import with same var/module at line {i}: {repr(line)}")
                return True
    
    logger.debug("No matching import found")
    return False


def _is_other_declaration_duplicate(file_lines: List[str], added_line: str) -> bool:
    """Check if non-import declarations are duplicates."""
    declaration_patterns = [
        r'const\s+(\w+)\s*=',  # const x =
        r'let\s+(\w+)\s*=',    # let x =
        r'var\s+(\w+)\s*=',    # var x =
        r'function\s+(\w+)\s*\(',  # function x(
        r'class\s+(\w+)\s*{',  # class x {
        r'interface\s+(\w+)\s*{',  # interface x {
        r'type\s+(\w+)\s*=',   # type x =
        r'enum\s+(\w+)\s*{',   # enum x {
    ]
    
    for pattern in declaration_patterns:
        match = re.search(pattern, added_line)
        if match:
            declaration_name = match.group(1)
            
            # Check if this exact declaration already exists in the file
            for line in file_lines:
                if re.search(pattern.replace(r'(\w+)', re.escape(declaration_name)), line):
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
        return False
    
    return True


def _check_expected_content_match(file_lines: List[str], new_lines: List[str], pos: int, ignore_whitespace: bool) -> bool:
    """Check if the expected content after applying the hunk is already present."""
    if pos + len(new_lines) > len(file_lines):
        logger.debug(f"Not enough lines to compare at position {pos}")
        return False
    
    file_slice = file_lines[pos:pos+len(new_lines)]
    
    # Normalize lines for comparison
    if ignore_whitespace:
        normalized_file = [normalize_line_for_comparison(line) for line in file_slice]
        normalized_new = [normalize_line_for_comparison(line) for line in new_lines]
    else:
        normalized_file = [line.rstrip('\n\r') for line in file_slice]
        normalized_new = [line.rstrip('\n\r') for line in new_lines]
    
    # Check if they match
    matches = normalized_file == normalized_new
    
    if not matches:
        logger.debug(f"Content mismatch at position {pos}:")
        logger.debug(f"  File ({len(normalized_file)} lines): {normalized_file[:3]}...")
        logger.debug(f"  Expected ({len(normalized_new)} lines): {normalized_new[:3]}...")
    
    logger.debug(f"Content match at position {pos}: {matches}")
    return matches


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
