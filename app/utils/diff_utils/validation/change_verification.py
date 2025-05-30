"""
Utilities for verifying that changes were actually applied.
"""

import logging
from typing import Dict, List, Any, Tuple, Optional

logger = logging.getLogger("ZIYA")

def verify_changes_applied(original_content: str, modified_content: str, diff_content: str) -> bool:
    """
    Verify that changes were actually applied by comparing original and modified content.
    
    Args:
        original_content: The original file content
        modified_content: The modified file content
        diff_content: The diff that was applied
        
    Returns:
        True if changes were applied, False otherwise
    """
    # If content is identical, no changes were applied
    if original_content == modified_content:
        logger.warning("No changes detected in file content after diff application")
        return False
    
    # Check if the diff is non-trivial (contains actual changes)
    has_changes = False
    for line in diff_content.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            has_changes = True
            break
        if line.startswith('-') and not line.startswith('---'):
            has_changes = True
            break
    
    if not has_changes:
        logger.warning("Diff contains no actual changes")
        return False
    
    # Content is different and diff has changes, so changes were applied
    return True

def verify_hunk_changes(original_content: str, modified_content: str, hunks: List[Dict[str, Any]]) -> Dict[int, Tuple[bool, str]]:
    """
    Verify which hunks were actually applied by comparing original and modified content.
    
    Args:
        original_content: The original file content
        modified_content: The modified file content
        hunks: List of hunks that were applied
        
    Returns:
        Dictionary mapping hunk IDs to tuples of (success, reason)
    """
    results = {}
    
    # If content is identical, no hunks were applied
    if original_content == modified_content:
        logger.warning("No changes detected in file content after diff application")
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk.get('number', i)
            results[hunk_id] = (False, "No changes detected in file content")
        return results
    
    # For each hunk, check if its changes are reflected in the modified content
    for i, hunk in enumerate(hunks, 1):
        hunk_id = hunk.get('number', i)
        
        # Extract added lines from this hunk
        added_lines = []
        for line in hunk.get('old_block', []):
            if line.startswith('+'):
                added_lines.append(line[1:])
        
        # Extract removed lines from this hunk
        removed_lines = []
        for line in hunk.get('old_block', []):
            if line.startswith('-'):
                removed_lines.append(line[1:])
        
        # Check if added lines are in modified content but not original
        added_found = False
        for line in added_lines:
            if line.strip() and line in modified_content and line not in original_content:
                added_found = True
                results[hunk_id] = (True, f"Found added line in modified content")
                break
        
        # If no added lines were found, check for removed lines
        if not added_found and not results.get(hunk_id):
            removed_found = False
            for line in removed_lines:
                if line.strip() and line in original_content and line not in modified_content:
                    removed_found = True
                    results[hunk_id] = (True, f"Found removed line no longer in modified content")
                    break
            
            # If no removed lines were found either, this hunk might not have been applied
            if not removed_found:
                # Special case: if this is a whitespace-only change, we can't reliably detect it
                # So we'll assume it was applied if there are no substantive changes
                if (not any(line.strip() for line in added_lines) and 
                    not any(line.strip() for line in removed_lines)):
                    results[hunk_id] = (True, "Whitespace-only changes assumed applied")
                else:
                    results[hunk_id] = (False, "Could not verify hunk changes in modified content")
    
    return results
