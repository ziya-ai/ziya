"""
Utilities for handling whitespace-specific changes in diffs.
"""

import re
import difflib
from typing import List, Tuple, Optional

from app.utils.logging_utils import logger

def normalize_whitespace(content: str) -> str:
    """
    Normalize whitespace in content to make whitespace-only changes more detectable.
    
    Args:
        content: The content to normalize
        
    Returns:
        Normalized content
    """
    # Replace tabs with spaces (4 spaces per tab)
    content = content.replace('\t', '    ')
    
    # Normalize line endings
    content = content.replace('\r\n', '\n')
    
    # Handle invisible Unicode characters
    content = content.replace('\u200b', '')  # Zero-width space
    content = content.replace('\u200c', '')  # Zero-width non-joiner
    content = content.replace('\u200d', '')  # Zero-width joiner
    content = content.replace('\u2060', '')  # Word joiner
    content = content.replace('\ufeff', '')  # Zero-width no-break space (BOM)
    
    return content

def is_whitespace_only_change(old_line: str, new_line: str) -> bool:
    """
    Check if the difference between two lines is only whitespace.
    
    Args:
        old_line: Original line
        new_line: New line
        
    Returns:
        True if the only difference is whitespace
    """
    # Handle tab vs space differences
    normalized_old = old_line.replace('\t', '    ')
    normalized_new = new_line.replace('\t', '    ')
    
    # If they're equal after tab normalization, it's a whitespace change
    if normalized_old == normalized_new:
        return True
    
    # Remove all whitespace and compare
    old_no_space = re.sub(r'\s+', '', old_line)
    new_no_space = re.sub(r'\s+', '', new_line)
    
    return old_no_space == new_no_space

def extract_whitespace_changes(old_content: str, new_content: str) -> List[Tuple[int, str, str]]:
    """
    Extract lines that differ only in whitespace.
    
    Args:
        old_content: Original content
        new_content: New content
        
    Returns:
        List of tuples (line_number, old_line, new_line)
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    
    whitespace_changes = []
    
    # Find common prefix length
    min_len = min(len(old_lines), len(new_lines))
    for i in range(min_len):
        if old_lines[i] != new_lines[i] and is_whitespace_only_change(old_lines[i], new_lines[i]):
            whitespace_changes.append((i+1, old_lines[i], new_lines[i]))
    
    return whitespace_changes

def apply_whitespace_changes(content: str, changes: List[Tuple[int, str, str]]) -> str:
    """
    Apply whitespace-only changes to content.
    
    Args:
        content: Original content
        changes: List of (line_number, old_line, new_line) tuples
        
    Returns:
        Content with whitespace changes applied
    """
    lines = content.splitlines()
    
    for line_num, old_line, new_line in changes:
        if line_num <= len(lines) and lines[line_num-1] == old_line:
            lines[line_num-1] = new_line
    
    return '\n'.join(lines)

def is_whitespace_only_diff(diff_content: str) -> bool:
    """
    Check if a diff contains only whitespace changes.
    
    Args:
        diff_content: The diff content to check
        
    Returns:
        True if the diff only contains whitespace changes
    """
    # Extract the actual changes from the diff
    changes = []
    for line in diff_content.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            changes.append(('add', line[1:]))
        elif line.startswith('-') and not line.startswith('---'):
            changes.append(('remove', line[1:]))
    
    # Group additions and removals
    additions = [line for op, line in changes if op == 'add']
    removals = [line for op, line in changes if op == 'remove']
    
    # If we have different numbers of additions and removals, it's not just whitespace
    if len(additions) != len(removals):
        return False
    
    # Check each pair of addition/removal
    for add_line, remove_line in zip(additions, removals):
        if not is_whitespace_only_change(remove_line, add_line):
            return False
    
    return True

def process_whitespace_changes(original_content: str, diff_content: str) -> Optional[str]:
    """
    Process whitespace changes from a diff and apply them to the original content.
    
    Args:
        original_content: Original file content
        diff_content: Diff content to apply
        
    Returns:
        Content with whitespace changes applied, or None if not a whitespace-only diff
    """
    # Quick check if this is a whitespace-only diff
    if not is_whitespace_only_diff(diff_content):
        return None
    
    # Parse the diff to extract line numbers and changes
    whitespace_changes = []
    current_line = 0
    
    # Split the diff into lines for processing
    diff_lines = diff_content.splitlines()
    
    # First pass: identify all removed and added lines
    removed_lines = []
    added_lines = []
    
    for i, line in enumerate(diff_lines):
        if line.startswith('-') and not line.startswith('---'):
            removed_lines.append((i, line[1:]))
        elif line.startswith('+') and not line.startswith('+++'):
            added_lines.append((i, line[1:]))
    
    # Second pass: match removed and added lines that are whitespace-only changes
    matched_indices = set()
    
    for r_idx, (r_line_idx, r_line) in enumerate(removed_lines):
        for a_idx, (a_line_idx, a_line) in enumerate(added_lines):
            if a_idx in matched_indices:
                continue
                
            if is_whitespace_only_change(r_line, a_line):
                # Find the line number in the original file
                line_num = 0
                for i in range(r_line_idx):
                    if diff_lines[i].startswith('@@'):
                        match = re.search(r'@@ -(\d+)', diff_lines[i])
                        if match:
                            line_num = int(match.group(1)) - 1
                    elif diff_lines[i].startswith(' '):
                        line_num += 1
                    elif diff_lines[i].startswith('-') and not diff_lines[i].startswith('---'):
                        line_num += 1
                
                whitespace_changes.append((line_num, r_line, a_line))
                matched_indices.add(a_idx)
                break
    
    # Apply the whitespace changes
    if whitespace_changes:
        logger.info(f"Applying {len(whitespace_changes)} whitespace-only changes")
        return apply_whitespace_changes(original_content, whitespace_changes)
    
    # If we couldn't extract changes, try a direct approach
    original_lines = original_content.splitlines()
    expected_lines = original_lines.copy()
    
    # Apply the diff directly
    try:
        # Create a temporary file with the original content
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            temp_file.write(original_content)
            temp_path = temp_file.name
        
        try:
            # Apply the diff using the patch command
            import subprocess
            patch_process = subprocess.run(
                ['patch', '-p1', '--forward', '--no-backup-if-mismatch', '-i', '-'],
                input=diff_content,
                encoding='utf-8',
                cwd=os.path.dirname(temp_path),
                capture_output=True,
                text=True
            )
            
            # Read the patched content
            with open(temp_path, 'r') as f:
                patched_content = f.read()
            
            # Check if the changes are whitespace-only
            whitespace_changes = extract_whitespace_changes(original_content, patched_content)
            if len(whitespace_changes) > 0:
                logger.info(f"Detected {len(whitespace_changes)} whitespace-only changes")
                return patched_content
        finally:
            # Clean up the temporary file
            os.unlink(temp_path)
    except Exception as e:
        logger.error(f"Error applying whitespace changes: {str(e)}")
    
    # If we get here, it's not a whitespace-only change
    return None
