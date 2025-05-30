"""
Utilities for parsing diff files.
"""

import os
import re
from typing import List, Dict, Optional, Any, Tuple

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..core.utils import normalize_escapes

def extract_target_file_from_diff(diff_content: str) -> Optional[str]:
    """
    Extract the target file path from a git diff.
    Returns None if no valid target file path is found.
    
    Args:
        diff_content: The diff content to parse
        
    Returns:
        The target file path, or None if not found
    """
    if not diff_content:
        return None
        
    lines = diff_content.splitlines()
    for line in lines:
        # For new files (with standard format)
        if line.startswith('+++ b/'):
            return line[6:]
            
        # For new files (with /dev/null format)
        if line.startswith('+++ ') and not line.startswith('+++ b/') and not line.startswith('+++ /dev/null'):
            # Extract the path without any prefix
            return line[4:].strip()
            
        # For deleted files
        if line.startswith('--- a/'):
            return line[6:]
            
        # Check diff --git line as fallback
        if line.startswith('diff --git'):
            parts = line.split(' b/', 1)
            if len(parts) > 1:
                return parts[1]
                
    return None

def split_combined_diff(diff_content: str) -> List[str]:
    """
    Split a combined diff containing multiple files into individual file diffs.
    Returns a list of individual diff strings.
    
    Args:
        diff_content: The combined diff content
        
    Returns:
        A list of individual diff strings
    """
    logger.debug(f"split_combined_diff input first 10 lines:\n{diff_content.splitlines()[:10]}")
    
    if not diff_content:
        return []
    
    # Check if the diff already has diff --git lines
    has_diff_git_lines = bool(re.search(r'(?m)^diff --git ', diff_content))
    
    if has_diff_git_lines:
        # Split on diff --git lines
        parts = re.split(r'(?m)^diff --git ', diff_content)
        
        # First part might be empty or contain header info
        if parts and not parts[0].strip():
            parts.pop(0)
            
        # Reconstruct the individual diffs
        diffs = []
        for part in parts:
            if part.strip():
                # Fix malformed headers by ensuring proper format
                fixed_part = 'diff --git ' + part
                logger.debug(f"split_combined_diff reconstructed diff first 10 lines:\n{fixed_part.splitlines()[:10]}")
                
                # Check if the diff header is properly formatted
                lines = fixed_part.splitlines()
                if len(lines) >= 3:
                    # Check if we already have a +++ line and its position
                    plus_line_index = None
                    for i, line in enumerate(lines[:5]):  # Check first 5 lines to be safe
                        if line.startswith('+++ '):
                            plus_line_index = i
                            break
                    
                    has_plus_line = plus_line_index is not None
                    
                    # Ensure the header has proper file paths
                    if not (lines[0].startswith('diff --git') and 
                           (lines[1].startswith('--- ') or lines[1].startswith('--- a/')) and
                           has_plus_line):
                        
                        # Try to extract file paths from the diff --git line
                        match = re.match(r'diff --git a/(.*) b/(.*)', lines[0])
                        if match:
                            file_path = match.group(1)
                            # Fix the header lines
                            if not lines[1].startswith('--- '):
                                lines[1] = f'--- a/{file_path}'
                            
                            # Only add +++ line if it doesn't exist
                            if not has_plus_line:
                                # Find where to insert the +++ line
                                if len(lines) > 2:
                                    # Insert after the --- line
                                    new_lines = lines[:2] + [f'+++ b/{file_path}'] + lines[2:]
                                    fixed_part = '\n'.join(new_lines)
                            # If +++ line exists but is not in the right position, don't modify it
                            
                            logger.debug(f"split_combined_diff fixed header first 10 lines:\n{fixed_part.splitlines()[:10]}")
                
                diffs.append(fixed_part)
    else:
        # This is a simple diff without diff --git lines
        # Just return the original content as a single diff
        diffs = [diff_content]
        
    return diffs

def parse_unified_diff(diff_content: str) -> List[Dict[str, Any]]:
    """
    Parse a unified diff format and extract hunks with their content.
    If we can't parse anything, we return an empty list.
    
    Args:
        diff_content: The diff content to parse
        
    Returns:
        A list of dictionaries representing hunks
    """
    if not diff_content:
        return []
        
    hunks = []
    current_hunk = None
    
    for line in diff_content.splitlines():
        if line.startswith('@@ '):
            # Start of a new hunk
            if current_hunk:
                hunks.append(current_hunk)
                
            # Parse the hunk header
            match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if match:
                old_start = int(match.group(1))
                old_count = int(match.group(2))
                new_start = int(match.group(3))
                new_count = int(match.group(4))
                
                current_hunk = {
                    'old_start': old_start,
                    'old_count': old_count,
                    'new_start': new_start,
                    'new_count': new_count,
                    'header': line,
                    'content': []
                }
        elif current_hunk is not None:
            current_hunk['content'].append(line)
            
    # Add the last hunk
    if current_hunk:
        hunks.append(current_hunk)
        
    return hunks

def strip_leading_dotslash(path: str) -> str:
    """
    Strip leading ./ from a path.
    
    Args:
        path: The path to strip
        
    Returns:
        The path without leading ./
    """
    if path.startswith('./'):
        return path[2:]
    return path

def parse_unified_diff_exact_plus(diff_content: str, target_file: str) -> List[Dict[str, Any]]:
    """
    Parse a unified diff format and extract hunks with their content.
    If we can't parse anything, we return an empty list.
    
    This version handles embedded diff markers correctly by using a more robust parsing approach.
    
    Args:
        diff_content: The diff content to parse
        target_file: The target file path
        
    Returns:
        A list of dictionaries representing hunks
    """
    logger.debug(f"parse_unified_diff_exact_plus input diff first 10 lines:\n{diff_content.splitlines()[:10]}")
    lines = diff_content.splitlines()
    logger.debug(f"Parsing diff with {len(lines)} lines:\n{diff_content}")
    hunks = []
    current_hunk = None
    in_hunk = False
    skip_file = True
    seen_hunks = set()

    # fixme: import ziya project directory if specified on invocation cli
    rel_path = os.path.relpath(target_file, os.getcwd())
    rel_path = strip_leading_dotslash(rel_path)

    i = 0
    while i < len(lines):
        line = lines[i]
        logger.debug(f"parse_unified_diff_exact_plus => line[{i}]: {line!r}")

        if line.startswith('diff --git'):
            i += 1
            continue
            
        # Skip diff header lines, but deduplicate +++ lines
        if line.startswith(('--- ', '+++ ')) and (i == 0 or lines[i-1] != line):
            # Skip diff header lines, but only outside of hunks
            # This is important for handling embedded diff markers in content
            if not in_hunk:
                i += 1
                continue

        # Handle index lines and other git metadata
        if line.startswith('index ') or line.startswith('new file mode ') or line.startswith('deleted file mode '):
            i += 1
            continue

        if line.startswith('@@ '):
            match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:\s+Hunk #(\d+))?', line)
            hunk_num = int(match.group(5)) if match and match.group(5) else len(hunks) + 1
            if match:
                old_start = int(match.group(1))
                # Validate line numbers
                if old_start < 1:
                    logger.warning(f"Invalid hunk header - old_start ({old_start}) < 1")
                    old_start = 1

                # Use default of 1 for count if not specified
                old_count = int(match.group(2)) if match.group(2) else 1

                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) else 1

                # Use original hunk number if present in header
                if match.group(5):
                    hunk_num = int(match.group(5))

                hunk = {
                    'old_start': old_start,
                    'old_count': old_count,
                    'new_start': new_start,
                    'new_count': new_count,
                    'number': hunk_num,
                    'lines': [],
                    'old_block': [],
                    'original_hunk': hunk_num,  # Store original hunk number
                    'new_lines': [],
                    'old_lines': old_count,     # Store old line count for patch application
                    'removed_lines': [],        # Track removed lines
                    'added_lines': [],          # Track added lines
                    'header': line              # Store the original header
                }

                # Start collecting content for this hunk
                in_hunk = True
                hunks.append(hunk)  # Add the hunk to our list immediately
                current_hunk = hunk

            i += 1
            # Validate the hunk header against the actual content
            # This helps catch malformed hunks early
            if current_hunk:
                # Count the number of lines in the hunk
                hunk_lines = []
                j = i
                while j < len(lines) and lines[j].startswith((' ', '+', '-', '\\')):
                    hunk_lines.append(lines[j])
                    j += 1
                current_hunk['expected_line_count'] = len(hunk_lines)
            continue

        if in_hunk:
            # End of hunk reached if we see a line that doesn't start with ' ', '+', '-', or '\'
            if not line.startswith((' ', '+', '-', '\\')):
                in_hunk = False
                if current_hunk:
                    # Check if this hunk is complete and unique
                    hunk_key = (tuple(current_hunk['old_block']), tuple(current_hunk['new_lines']))
                    if hunk_key not in seen_hunks:
                        seen_hunks.add(hunk_key)
                i += 1
                continue
            if current_hunk:
                current_hunk['lines'].append(line)
                if line.startswith('-'):
                    text = line[1:].rstrip('\r\n')
                    current_hunk['old_block'].append(text)  
                    current_hunk['removed_lines'].append(text)
                elif line.startswith('+'):
                    text = line[1:].rstrip('\r\n')
                    current_hunk['new_lines'].append(text)
                    current_hunk['added_lines'].append(text)  # Store without prefix
                elif line.startswith(' '):
                    text = line[1:].rstrip('\r\n')
                    current_hunk['new_lines'].append(text)
                    current_hunk['old_block'].append(text)
                elif line.startswith('\\'):
                    # Handle "No newline at end of file" marker
                    if "No newline at end of file" in line:
                        current_hunk['missing_newline'] = True
                        logger.debug(f"Found 'No newline at end of file' marker in hunk {current_hunk['number']}")
                # Skip other lines starting with '\'
            
            # Always increment the counter inside the hunk processing
            i += 1
        else:
            # Only increment if we're not in a hunk
            i += 1
    
    # Sort hunks by old_start to ensure they're processed in the correct order
    hunks.sort(key=lambda h: h['old_start'])
    
    # Check if the diff has a "No newline at end of file" marker
    has_no_newline_marker = "No newline at end of file" in diff_content
    
    # If the diff has a "No newline at end of file" marker and we have hunks, ensure the last hunk has the flag set
    if has_no_newline_marker and hunks and not hunks[-1].get('missing_newline'):
        logger.debug(f"Setting missing_newline flag on last hunk due to marker in diff")
        hunks[-1]['missing_newline'] = True
    
    return hunks
