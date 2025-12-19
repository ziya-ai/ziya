"""
Utilities for reversing git diffs.
"""

import re
import subprocess
import tempfile
import os
from typing import List, Dict, Any, Tuple

def reverse_diff(diff_content: str) -> str:
    """
    Reverse a git diff by swapping additions and deletions.
    
    In a reversed diff:
    - Lines that were added (+) become deletions (-)
    - Lines that were deleted (-) become additions (+)
    - Within each hunk, the order of changes must be adjusted so that
      the new additions (old deletions) come before new deletions (old additions)
    
    Args:
        diff_content: The original diff content
        
    Returns:
        The reversed diff content
    """
    if not diff_content:
        return diff_content
    
    lines = diff_content.splitlines()
    reversed_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.startswith('@@'):
            # Reverse hunk header: swap old and new ranges
            match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@(.*)$', line)
            if match:
                old_start, old_count, new_start, new_count, context = match.groups()
                reversed_line = f"@@ -{new_start},{new_count} +{old_start},{old_count} @@{context}"
                reversed_lines.append(reversed_line)
            else:
                # Handle single-line format: @@ -1 +1 @@
                match = re.match(r'^@@ -(\d+) \+(\d+) @@(.*)$', line)
                if match:
                    old_start, new_start, context = match.groups()
                    reversed_line = f"@@ -{new_start} +{old_start} @@{context}"
                    reversed_lines.append(reversed_line)
                else:
                    # Keep malformed headers as-is
                    reversed_lines.append(line)
            i += 1
            
            # Process the hunk content - collect and reorder changes
            hunk_lines = []
            while i < len(lines) and not lines[i].startswith('@@') and not lines[i].startswith('diff '):
                hunk_lines.append(lines[i])
                i += 1
            
            # Process hunk lines in groups: context, then changes
            j = 0
            while j < len(hunk_lines):
                # Collect context lines
                while j < len(hunk_lines) and not hunk_lines[j].startswith('+') and not hunk_lines[j].startswith('-'):
                    if not hunk_lines[j].startswith('+++') and not hunk_lines[j].startswith('---'):
                        reversed_lines.append(hunk_lines[j])
                    else:
                        reversed_lines.append(hunk_lines[j])
                    j += 1
                
                # Collect a group of changes (consecutive +/- lines)
                deletions = []  # Original - lines (will become +)
                additions = []  # Original + lines (will become -)
                
                while j < len(hunk_lines) and (hunk_lines[j].startswith('+') or hunk_lines[j].startswith('-')):
                    if hunk_lines[j].startswith('+++') or hunk_lines[j].startswith('---'):
                        # File headers - keep as-is
                        reversed_lines.append(hunk_lines[j])
                    elif hunk_lines[j].startswith('+'):
                        additions.append(hunk_lines[j])
                    elif hunk_lines[j].startswith('-'):
                        deletions.append(hunk_lines[j])
                    j += 1
                
                # Output reversed changes: old deletions become additions first, then old additions become deletions
                for del_line in deletions:
                    reversed_lines.append('+' + del_line[1:])
                for add_line in additions:
                    reversed_lines.append('-' + add_line[1:])
        
        elif line.startswith('diff ') or line.startswith('---') or line.startswith('+++'):
            # Keep diff headers as-is
            reversed_lines.append(line)
            i += 1
        else:
            # Other lines (shouldn't happen in well-formed diffs)
            reversed_lines.append(line)
            i += 1
    
    return '\n'.join(reversed_lines)


def apply_reverse_diff_direct(original_content: str, expected_content: str, diff_content: str) -> Tuple[bool, str]:
    """
    Apply a reverse diff by directly computing what the original should be.
    
    This function takes a more direct approach: given the expected content (after forward apply)
    and the original diff, it computes what the content should be after reversing.
    
    The key insight is that if forward_apply(original, diff) = expected,
    then reverse_apply(expected, diff) should = original.
    
    We can verify this by checking if the reversed content matches the original.
    
    Args:
        original_content: The original file content (before any diff was applied)
        expected_content: The expected content after forward diff application
        diff_content: The original (non-reversed) diff content
        
    Returns:
        Tuple of (success, result_content)
    """
    # Parse the diff to extract the changes
    lines = diff_content.splitlines()
    
    # For each hunk, we need to:
    # 1. Find the "new" lines (lines that were added in the forward diff)
    # 2. Find the "old" lines (lines that were removed in the forward diff)
    # 3. In the expected content, replace the "new" lines with the "old" lines
    
    expected_lines = expected_content.splitlines(keepends=True)
    result_lines = expected_lines.copy()
    
    # Track offset as we make changes
    offset = 0
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.startswith('@@'):
            # Parse hunk header
            match = re.match(r'^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', line)
            if match:
                old_start = int(match.group(1))
                new_start = int(match.group(3))
                
                # Collect the hunk content
                i += 1
                old_lines = []  # Lines that were in the original (marked with -)
                new_lines = []  # Lines that are in the new version (marked with +)
                context_before = []
                context_after = []
                in_changes = False
                
                while i < len(lines) and not lines[i].startswith('@@') and not lines[i].startswith('diff '):
                    hunk_line = lines[i]
                    if hunk_line.startswith('-') and not hunk_line.startswith('---'):
                        old_lines.append(hunk_line[1:])
                        in_changes = True
                    elif hunk_line.startswith('+') and not hunk_line.startswith('+++'):
                        new_lines.append(hunk_line[1:])
                        in_changes = True
                    elif hunk_line.startswith(' ') or (not hunk_line.startswith('+') and not hunk_line.startswith('-') and not hunk_line.startswith('\\') and not hunk_line.startswith('diff') and not hunk_line.startswith('---') and not hunk_line.startswith('+++')):
                        if not in_changes:
                            context_before.append(hunk_line[1:] if hunk_line.startswith(' ') else hunk_line)
                        else:
                            context_after.append(hunk_line[1:] if hunk_line.startswith(' ') else hunk_line)
                    i += 1
                
                # Now we need to find where the new_lines are in the expected content
                # and replace them with old_lines
                # The position should be around new_start (adjusted for offset)
                
                search_start = max(0, new_start - 1 + offset - len(context_before))
                
                # Find the new_lines in the result
                if new_lines:
                    # Look for the new_lines in the result
                    for pos in range(search_start, min(len(result_lines), search_start + 50)):
                        # Check if new_lines match at this position
                        match_found = True
                        for j, new_line in enumerate(new_lines):
                            if pos + j >= len(result_lines):
                                match_found = False
                                break
                            result_line = result_lines[pos + j].rstrip('\n\r')
                            if result_line != new_line.rstrip('\n\r'):
                                match_found = False
                                break
                        
                        if match_found:
                            # Replace new_lines with old_lines
                            old_lines_with_endings = [l + '\n' for l in old_lines]
                            result_lines[pos:pos + len(new_lines)] = old_lines_with_endings
                            offset += len(old_lines) - len(new_lines)
                            break
                elif old_lines:
                    # Pure deletion in forward = pure addition in reverse
                    # We need to add the old_lines back
                    insert_pos = new_start - 1 + offset
                    old_lines_with_endings = [l + '\n' for l in old_lines]
                    result_lines[insert_pos:insert_pos] = old_lines_with_endings
                    offset += len(old_lines)
                
                continue
        
        i += 1
    
    result_content = ''.join(result_lines)
    success = result_content.rstrip() == original_content.rstrip()
    
    return success, result_content


def apply_reverse_patch(diff_content: str, file_path: str) -> bool:
    """
    Apply a patch in reverse using the system patch command with -R flag.
    
    This is more reliable for reverse application than going through the
    full diff application pipeline, which has fuzzy matching logic designed
    for forward application.
    
    Args:
        diff_content: The original (non-reversed) diff content
        file_path: Path to the file to patch
        
    Returns:
        True if successful, False otherwise
    """
    # Write the diff to a temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False) as f:
        f.write(diff_content)
        diff_file = f.name
    
    try:
        # Try patch -R first
        result = subprocess.run(
            ['patch', '-R', '-p0', '--no-backup-if-mismatch', file_path, diff_file],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            return True
        
        # If patch -R failed, try with more lenient options
        result = subprocess.run(
            ['patch', '-R', '-p0', '--no-backup-if-mismatch', '-l', file_path, diff_file],
            capture_output=True,
            text=True
        )
        
        return result.returncode == 0
    finally:
        os.unlink(diff_file)


def reverse_hunk(hunk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reverse a single parsed hunk.
    
    Args:
        hunk: The hunk dictionary to reverse
        
    Returns:
        The reversed hunk dictionary
    """
    reversed_hunk = hunk.copy()
    
    # Swap old and new ranges
    reversed_hunk['old_start'] = hunk['new_start']
    reversed_hunk['old_count'] = hunk['new_count']
    reversed_hunk['new_start'] = hunk['old_start']
    reversed_hunk['new_count'] = hunk['old_count']
    
    # Reverse the header
    if 'header' in hunk:
        reversed_hunk['header'] = reverse_diff(hunk['header'])
    
    # Reverse the content - need to reorder changes
    if 'content' in hunk:
        reversed_content = []
        i = 0
        content = hunk['content']
        
        while i < len(content):
            # Collect context lines
            while i < len(content) and not content[i].startswith('+') and not content[i].startswith('-'):
                reversed_content.append(content[i])
                i += 1
            
            # Collect changes
            deletions = []
            additions = []
            while i < len(content) and (content[i].startswith('+') or content[i].startswith('-')):
                if content[i].startswith('+'):
                    additions.append(content[i])
                else:
                    deletions.append(content[i])
                i += 1
            
            # Output reversed: deletions become additions, additions become deletions
            for del_line in deletions:
                reversed_content.append('+' + del_line[1:])
            for add_line in additions:
                reversed_content.append('-' + add_line[1:])
        
        reversed_hunk['content'] = reversed_content
    
    return reversed_hunk
