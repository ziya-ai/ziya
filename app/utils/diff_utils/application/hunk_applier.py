"""
Hunk application utilities.
"""

import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("ZIYA")
from ..validation.validators import is_hunk_already_applied

def apply_hunk(file_lines: List[str], hunk: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Apply a single hunk to file lines.
    
    Args:
        file_lines: The file lines to modify
        hunk: The hunk to apply
        
    Returns:
        A tuple of (success, modified_lines)
    """
    logger.debug(f"Applying hunk at source lines {hunk.get('src_start', hunk.get('old_start', 1))}-{hunk.get('src_start', hunk.get('old_start', 1)) + hunk.get('src_count', hunk.get('old_count', 0)) - 1}")
    
    # Make a copy of the file lines
    modified_lines = file_lines.copy()
    
    # Check if the hunk is already applied
    if is_hunk_already_applied(file_lines, hunk, 0, ignore_whitespace=True):
        logger.info("Hunk is already applied")
        return True, modified_lines
    
    # Get the source line range
    src_start = hunk.get('src_start', hunk.get('old_start', 1)) - 1  # Convert to 0-based index
    src_end = src_start + hunk.get('src_count', hunk.get('old_count', 0))
    
    # Check if the source range is valid
    if src_start < 0 or src_end > len(file_lines):
        logger.error(f"Invalid source range: {src_start}-{src_end} (file has {len(file_lines)} lines)")
        return False, file_lines

    # Extract the lines to remove and add
    removed_lines = []
    added_lines = []
    
    for line in hunk.get('lines', []):
        if line.startswith('-'):
            removed_lines.append(line[1:])
        elif line.startswith('+'):
            added_lines.append(line[1:])
    
    # Check if the lines to remove match the file content
    file_section = file_lines[src_start:src_end]
    
    # Count the context lines (lines that are not added or removed)
    context_lines = [line for line in hunk.get('lines', []) if not line.startswith('+') and not line.startswith('-')]
    
    # Check if we have the right number of lines
    if len(file_section) != len(removed_lines) + len(context_lines):
        logger.warning(f"Line count mismatch: file has {len(file_section)} lines, hunk expects {len(removed_lines) + len(context_lines)}")
        
        # Try to find the best match for the hunk
        best_match = find_best_match(file_lines, hunk)
        if best_match is not None:
            logger.info(f"Found better match at line {best_match + 1}")
            src_start = best_match
            src_end = src_start + hunk['src_count']
            file_section = file_lines[src_start:src_end]
    
    # Create the new section by replacing removed lines with added lines
    new_section = []
    file_idx = 0
    hunk_idx = 0
    
    while file_idx < len(file_section) and hunk_idx < len(hunk['lines']):
        hunk_line = hunk['lines'][hunk_idx]
        
        if hunk_line.startswith('-'):
            # This is a line to remove
            if file_idx < len(file_section) and file_section[file_idx].rstrip() == hunk_line[1:].rstrip():
                # Skip this line (remove it)
                file_idx += 1
            else:
                # The line to remove doesn't match the file content
                logger.warning(f"Line to remove doesn't match: '{file_section[file_idx].rstrip()}' != '{hunk_line[1:].rstrip()}'")
                return False, file_lines
            
            hunk_idx += 1
        elif hunk_line.startswith('+'):
            # This is a line to add
            new_section.append(hunk_line[1:])
            hunk_idx += 1
        else:
            # This is a context line
            if file_idx < len(file_section) and file_section[file_idx].rstrip() == hunk_line.rstrip():
                # Keep this line
                new_section.append(file_section[file_idx])
                file_idx += 1
                hunk_idx += 1
            else:
                # The context line doesn't match the file content
                logger.warning(f"Context line doesn't match: '{file_section[file_idx].rstrip()}' != '{hunk_line.rstrip()}'")
                return False, file_lines
    
    # Replace the old section with the new section
    modified_lines = file_lines[:src_start] + new_section + file_lines[src_end:]
    
    return True, modified_lines

def find_best_match(file_lines: List[str], hunk: Dict[str, Any]) -> Optional[int]:
    """
    Find the best match for a hunk in the file.
    
    Args:
        file_lines: The file lines
        hunk: The hunk to match
        
    Returns:
        The line index of the best match, or None if no match found
    """
    # Extract the context lines from the hunk
    context_lines = []
    
    for line in hunk['lines']:
        if not line.startswith('+') and not line.startswith('-'):
            context_lines.append(line)
    
    if not context_lines:
        return None
    
    # Try to find the best match for the context lines
    best_match = None
    best_score = 0
    
    for i in range(len(file_lines) - len(context_lines) + 1):
        file_section = file_lines[i:i+len(context_lines)]
        
        # Calculate the match score
        score = 0
        for file_line, context_line in zip(file_section, context_lines):
            if file_line.rstrip() == context_line.rstrip():
                score += 1
        
        # Update the best match if this is better
        if score > best_score:
            best_score = score
            best_match = i
    
    # Only return the match if it's good enough
    if best_score >= len(context_lines) * 0.7:  # At least 70% match
        return best_match
    
    return None
