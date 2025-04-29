"""
Direct diff application implementation that follows the unified diff format exactly.
"""

import re
import os
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("ZIYA")

def apply_diff_directly(file_path: str, diff_content: str) -> str:
    """
    Apply a diff directly to a file by parsing the unified diff format exactly.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content in unified format
        
    Returns:
        The modified file content
    """
    logger.info(f"Applying diff directly to {file_path}")
    
    # Read the file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except FileNotFoundError:
        original_content = ""
    
    # Parse the diff into hunks
    hunks = parse_unified_diff(diff_content)
    if not hunks:
        logger.warning("No hunks found in diff")
        return original_content
    
    # Apply each hunk in order
    modified_content = original_content
    for hunk in hunks:
        modified_content = apply_hunk(modified_content, hunk)
    
    return modified_content

def parse_unified_diff(diff_content: str) -> List[Dict[str, Any]]:
    """
    Parse a unified diff into hunks.
    
    Args:
        diff_content: The diff content
        
    Returns:
        List of hunks
    """
    hunks = []
    lines = diff_content.splitlines()
    
    # Skip header lines
    i = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        i += 1
    
    # Parse each hunk
    while i < len(lines):
        if lines[i].startswith("@@"):
            # Parse hunk header
            match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[i])
            if not match:
                logger.warning(f"Invalid hunk header: {lines[i]}")
                i += 1
                continue
            
            old_start = int(match.group(1))
            old_count = int(match.group(2) or 1)
            new_start = int(match.group(3))
            new_count = int(match.group(4) or 1)
            
            # Extract hunk content
            hunk_content = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@"):
                hunk_content.append(lines[i])
                i += 1
            
            hunks.append({
                'old_start': old_start,
                'old_count': old_count,
                'new_start': new_start,
                'new_count': new_count,
                'content': hunk_content
            })
        else:
            i += 1
    
    return hunks

def apply_hunk(content: str, hunk: Dict[str, Any]) -> str:
    """
    Apply a hunk to the content.
    
    Args:
        content: The content to modify
        hunk: The hunk to apply
        
    Returns:
        The modified content
    """
    lines = content.splitlines(True)  # Keep line endings
    
    # Calculate the actual line numbers (1-based)
    old_start = max(1, hunk['old_start'])  # Ensure it's at least 1
    
    # Extract the lines to remove and add
    old_lines = []
    new_lines = []
    
    for line in hunk['content']:
        if line.startswith(' '):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith('-'):
            old_lines.append(line[1:])
        elif line.startswith('+'):
            new_lines.append(line[1:])
    
    # Ensure all lines have line endings but don't add extra blank lines
    for i in range(len(new_lines)):
        if not new_lines[i].endswith('\n'):
            new_lines[i] += '\n'
    
    # Find the exact position to apply the hunk
    position = find_hunk_position(lines, old_lines, old_start)
    if position is None or position < 0:
        logger.warning(f"Could not find position to apply hunk at line {old_start}")
        return content
    
    # Apply the hunk
    result = lines[:position] + new_lines + lines[position + len(old_lines):]
    
    # Clean up any extra blank lines that weren't in the original content or diff
    result = clean_up_blank_lines(result)
    
    return ''.join(result)

def clean_up_blank_lines(lines: List[str]) -> List[str]:
    """
    Clean up extra blank lines that weren't in the original content or diff.
    
    Args:
        lines: The content lines
        
    Returns:
        The cleaned up lines
    """
    # Remove trailing blank lines
    while lines and lines[-1].strip() == '':
        lines.pop()
    
    # Add back a single trailing newline
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    
    # Remove consecutive blank lines (more than one)
    i = 0
    while i < len(lines) - 1:
        if lines[i].strip() == '' and lines[i+1].strip() == '':
            # Keep track of consecutive blank lines
            j = i + 1
            while j < len(lines) and lines[j].strip() == '':
                j += 1
            
            # If we have more than one consecutive blank line, remove extras
            if j - i > 2:
                # Keep only one blank line
                del lines[i+2:j]
        i += 1
    
    return lines

def find_hunk_position(lines: List[str], old_lines: List[str], old_start: int) -> Optional[int]:
    """
    Find the position to apply a hunk.
    
    Args:
        lines: The content lines
        old_lines: The lines to replace
        old_start: The starting line number (1-based)
        
    Returns:
        The position (0-based) or None if not found
    """
    # First try the exact position
    position = old_start - 1  # Convert to 0-based
    if 0 <= position < len(lines):
        # Check if the lines match at the exact position
        if position + len(old_lines) <= len(lines):
            exact_match = True
            for i in range(len(old_lines)):
                if lines[position + i].rstrip('\r\n') != old_lines[i].rstrip('\r\n'):
                    exact_match = False
                    break
            
            if exact_match:
                return position
    
    # If exact position doesn't match, try to find the context
    context_size = 3  # Number of context lines to look for
    
    # Extract context lines from the beginning and end of old_lines
    if len(old_lines) >= 2 * context_size:
        prefix = [line.rstrip('\r\n') for line in old_lines[:context_size]]
        suffix = [line.rstrip('\r\n') for line in old_lines[-context_size:]]
        
        # Look for the context in the file
        for i in range(len(lines) - len(old_lines) + 1):
            prefix_match = True
            suffix_match = True
            
            for j in range(context_size):
                if i + j >= len(lines) or lines[i + j].rstrip('\r\n') != prefix[j]:
                    prefix_match = False
                    break
            
            for j in range(context_size):
                if i + len(old_lines) - context_size + j >= len(lines) or \
                   lines[i + len(old_lines) - context_size + j].rstrip('\r\n') != suffix[j]:
                    suffix_match = False
                    break
            
            if prefix_match and suffix_match:
                return i
    
    # If context matching fails, try fuzzy matching
    best_match = 0
    best_position = None
    
    for i in range(len(lines) - len(old_lines) + 1):
        matches = 0
        for j in range(len(old_lines)):
            if i + j < len(lines) and lines[i + j].rstrip('\r\n') == old_lines[j].rstrip('\r\n'):
                matches += 1
        
        match_ratio = matches / len(old_lines)
        if match_ratio > best_match:
            best_match = match_ratio
            best_position = i
    
    # Only accept fuzzy matches above a certain threshold
    if best_match >= 0.7:
        return best_position
    
    # Fall back to the original position if all else fails
    if old_start - 1 < len(lines):
        return old_start - 1
    
    return None
