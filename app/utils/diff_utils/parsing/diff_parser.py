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
        # For new files or modified files
        if line.startswith('+++ b/'):
            return line[6:]
            
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
    if not diff_content:
        return []
        
    # Split on diff --git lines
    parts = re.split(r'(?m)^diff --git ', diff_content)
    
    # First part might be empty or contain header info
    if parts and not parts[0].strip():
        parts.pop(0)
        
    # Reconstruct the individual diffs
    diffs = []
    for part in parts:
        if part.strip():
            diffs.append('diff --git ' + part)
            
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

        if line.startswith(('--- ', '+++ ')):
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
                    'removed_lines': [],  # Track removed lines
                    'added_lines': []     # Track added lines
                }

                # Start collecting content for this hunk
                in_hunk = True
                hunks.append(hunk)  # Add the hunk to our list immediately
                current_hunk = hunk

            i += 1
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
                    text = line[1:]
                    current_hunk['old_block'].append(text) # Add removed line to old_block
                    current_hunk['removed_lines'].append(text)
                elif line.startswith('+'):
                    text = line[1:]
                    current_hunk['new_lines'].append(text)
                    current_hunk['added_lines'].append(text)  # Store without prefix
                elif line.startswith(' '):
                    text = line[1:]
                    current_hunk['new_lines'].append(text)
                    current_hunk['old_block'].append(text)
                # Skip lines starting with '\'
            
            # Always increment the counter inside the hunk processing
            i += 1
        else:
            # Only increment if we're not in a hunk
            i += 1
    
    # Sort hunks by old_start to ensure they're processed in the correct order
    hunks.sort(key=lambda h: h['old_start'])
    
    return hunks
