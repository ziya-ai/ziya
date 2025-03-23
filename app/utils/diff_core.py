"""
Core utilities for handling diffs and patches.
This module contains the basic functionality for parsing and manipulating diffs.
"""

import os
import re
import json
import difflib
from typing import Dict, Optional, Union, List, Tuple, Any
from itertools import zip_longest

from app.utils.logging_utils import logger

class PatchApplicationError(Exception):
    """Custom exception for patch application failures"""
    def __init__(self, message: str, details: Dict):
        super().__init__(message)
        self.details = details

def normalize_escapes(text: str) -> str:
    """
    Normalize escape sequences in text to improve matching.
    This helps with comparing strings that have different escape sequence representations.
    """
    # Replace common escape sequences with placeholders
    replacements = {
        '\\n': '_NL_',
        '\\r': '_CR_',
        '\\t': '_TAB_',
        '\\"': '_QUOTE_',
        "\\'": '_SQUOTE_',
        '\\\\': '_BSLASH_'
    }
    
    result = text
    for esc, placeholder in replacements.items():
        result = result.replace(esc, placeholder)
    
    return result

def extract_target_file_from_diff(diff_content: str) -> Optional[str]:
    """
    Extract the target file path from a git diff.
    Returns None if no valid target file path is found.
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
    """
    logger.info(f"Splitting diff content of length {len(diff_content)}")
    diffs = []
    current_diff = []
    lines = diff_content.splitlines(True)  # Keep line endings
    
    # First, identify all actual diff headers
    diff_header_indices = []
    for i, line in enumerate(lines):
        if line.startswith('diff --git'):
            diff_header_indices.append(i)
    
    # If no diff headers found, treat the whole content as one diff
    if not diff_header_indices:
        return [diff_content]
    
    # Process each diff section
    for start_idx, end_idx in zip(diff_header_indices, diff_header_indices[1:] + [len(lines)]):
        current_diff = lines[start_idx:end_idx]
        diffs.append(''.join(current_diff))
        logger.info(f"Extracted diff from line {start_idx} to {end_idx-1}")
    
    return diffs

def clean_input_diff(diff_content: str) -> str:
    """
    Initial cleanup of diff content before parsing, with strict hunk enforcement.
    """
    logger.debug(diff_content)

    result_lines = []

    # Split into lines for processing
    lines = diff_content.splitlines()

    # Track the current file and hunk state
    current_file = None
    in_hunk = False
    skip_until_next_file = False

    # For the strict hunk approach
    old_count = 0
    new_count = 0
    minus_seen = 0
    plus_seen = 0

    def reset_hunk_state():
        nonlocal in_hunk, old_count, new_count, minus_seen, plus_seen
        in_hunk = False
        old_count = 0
        new_count = 0
        minus_seen = 0
        plus_seen = 0

    for line in lines:
        # Reset skip flag on new file header
        if line.startswith('diff --git'):
            skip_until_next_file = False
            current_file = None
            reset_hunk_state()
            result_lines.append(line)
            continue

        # Track file paths
        if line.startswith('--- '):
            parts = line.split(' ', 1)
            if len(parts) > 1:
                current_file = parts[1]
            else:
                current_file = None
            result_lines.append(line)
            continue

        if line.startswith('+++ '):
            result_lines.append(line)
            continue

        # Hunk header
        if line.startswith('@@'):
            # Close out any prior hunk
            reset_hunk_state()
            in_hunk = True
            skip_until_next_file = False

            result_lines.append(line)  # Keep the hunk header

            # Parse the line to find old_count/new_count
            match = re.match(r'^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@', line)
            if match:
                old_count = int(match.group(1)) if match.group(1) else 1
                new_count = int(match.group(1)) if match.group(1) else 1
            continue

        if skip_until_next_file:
            # We skip lines until next file/hunk
            continue

        # If we're inside a hunk, apply the strict approach
        if in_hunk:
            # Check if we've already read all minus and plus lines
            done_minus = (minus_seen >= old_count)
            done_plus = (plus_seen >= new_count)

            if line.startswith('-'):
                if not done_minus:
                    minus_seen += 1
                    result_lines.append(line)
                else:
                    # We have enough minus lines => ignore it
                    logger.debug(f"[clean_input_diff] ignoring extra '-' line: {line.rstrip()}")
                continue

            if line.startswith('+'):
                if not done_plus:
                    plus_seen += 1
                    result_lines.append(line)
                else:
                    # We have enough plus lines => ignore it
                    logger.debug(f"[clean_input_diff] ignoring extra '+' line: {line.rstrip()}")
                continue

            if line.startswith(' '):
                # context lines are always okay
                result_lines.append(line)
                continue

            # If we get here and it's not -, +, or space => presumably hunk is done
            reset_hunk_state()
            result_lines.append(line)
            continue

        # If not in a hunk, just pass the line along
        result_lines.append(line)

    # End of loop
    return '\n'.join(result_lines)

def normalize_diff(diff_content: str) -> str:
    """
    Normalize a diff for proper parsing and reconstruction.
    Handles incomplete hunks, context issues, line count mismatches, and embedded diff markers.
    """
    try:
        # Extract headers and hunk headers from original diff
        diff_lines = diff_content.splitlines()
        result = []
        i = 0
        
        # First, identify all actual diff headers and file paths
        diff_headers = []
        file_paths = []
        hunk_headers = []
        
        for i, line in enumerate(diff_lines):
            if line.startswith('diff --git'):
                diff_headers.append(i)
            elif line.startswith(('--- ', '+++ ')):
                file_paths.append(i)
            elif line.startswith('@@'):
                hunk_headers.append(i)
        
        # If we don't have proper headers, return the original
        if not diff_headers and not file_paths and not hunk_headers:
            return diff_content
            
        # Process the diff, preserving headers and hunks
        i = 0
        while i < len(diff_lines):
            line = diff_lines[i]
            
            # Keep all headers
            if line.startswith(('diff --git', 'index', '--- ', '+++ ')):
                result.append(line)
                i += 1
                continue
                
            # Process hunks
            if line.startswith('@@'):
                result.append(line)
                i += 1
                
                # Collect all lines in this hunk
                while i < len(diff_lines):
                    line = diff_lines[i]
                    
                    # If we hit another hunk header or file header, break
                    if line.startswith('@@') or line.startswith(('diff --git', '--- ', '+++ ')):
                        break
                        
                    # Only include lines that are valid diff content
                    if line.startswith((' ', '+', '-')):
                        result.append(line)
                    elif line.startswith('\\'):  # No newline at end of file marker
                        result.append(line)
                    
                    i += 1
                continue
                
            i += 1

        return '\n'.join(result) + '\n'
    except Exception as e:
        logger.error(f"Error normalizing diff: {str(e)}")
        return diff_content

def is_new_file_creation(diff_lines: List[str]) -> bool:
    """Determine if a diff represents a new file creation."""
    if not diff_lines:
        return False

    logger.debug(f"Analyzing diff lines for new file creation ({len(diff_lines)} lines):")
    for i, line in enumerate(diff_lines[:10]):
        logger.debug(f"Line {i}: {line[:100]}")  # Log first 100 chars of each line

    # Look for any indication this is a new file
    for i, line in enumerate(diff_lines[:10]):
        # Case 1: Standard git diff new file
        if line.startswith('@@ -0,0'):
            logger.debug("Detected new file from zero hunk marker")
            return True

        # Case 2: Empty source file indicator
        if line == '--- /dev/null':
            logger.debug("Detected new file from /dev/null source")
            return True

        # Case 3: New file mode
        if 'new file mode' in line:
            logger.debug("Detected new file from mode marker")
            return True

    logger.debug("No new file indicators found")
    return False

def strip_leading_dotslash(rel_path: str) -> str:
    """
    Remove leading '../' or './' segments from the relative path
    so it matches patch lines that are always 'frontend/...', not '../frontend/...'.
    """
    # Repeatedly strip leading '../' or './'
    pattern = re.compile(r'^\.\.?/')
    while pattern.match(rel_path):
        rel_path = rel_path[rel_path.index('/')+1:]
    return rel_path

def parse_unified_diff_exact_plus(diff_content: str, target_file: str) -> list[dict]:
    """
    Parse a unified diff format and extract hunks with their content.
    If we can't parse anything, we return an empty list.
    
    This version handles embedded diff markers correctly by using a more robust parsing approach.
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
                    'old_block': [],
                    'original_hunk': hunk_num,  # Store original hunk number
                    'new_lines': []
                }

                # Start collecting content for this hunk
                current_lines = []
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
                if line.startswith('-'):
                    text = line[1:]
                    current_hunk['old_block'].append(text)
                elif line.startswith('+'):
                    text = line[1:]
                    current_hunk['new_lines'].append(text)
                elif line.startswith(' '):
                    text = line[1:]
                    current_hunk['old_block'].append(text)
                    current_hunk['new_lines'].append(text)
                # Skip lines starting with '\'

        i += 1
    
    # Sort hunks by old_start to ensure they're processed in the correct order
    hunks.sort(key=lambda h: h['old_start'])
    
    return hunks

def calculate_block_similarity(file_block: list[str], diff_block: list[str]) -> float:
    """
    Calculate similarity between two blocks of text using difflib with improved handling
    of whitespace and special characters.
    
    Args:
        file_block: List of lines from the file
        diff_block: List of lines from the diff
        
    Returns:
        A ratio between 0.0 and 1.0 where 1.0 means identical
    """
    # Handle empty blocks
    if not file_block and not diff_block:
        return 1.0
    if not file_block or not diff_block:
        return 0.0
    
    # Normalize whitespace in both blocks
    file_str = '\n'.join(line.rstrip() for line in file_block)
    diff_str = '\n'.join(line.rstrip() for line in diff_block)
    
    # Use SequenceMatcher for fuzzy matching with improved junk detection
    matcher = difflib.SequenceMatcher(None, file_str, diff_str)
    
    # Get the similarity ratio
    ratio = matcher.ratio()
    
    # For blocks with special characters or escape sequences, do additional checks
    if ratio < 0.9 and (any('\\' in line for line in file_block) or any('\\' in line for line in diff_block)):
        # Try comparing with normalized escape sequences
        norm_file = '\n'.join(normalize_escapes(line) for line in file_block)
        norm_diff = '\n'.join(normalize_escapes(line) for line in diff_block)
        
        norm_matcher = difflib.SequenceMatcher(None, norm_file, norm_diff)
        norm_ratio = norm_matcher.ratio()
        
        # Use the better ratio
        ratio = max(ratio, norm_ratio)
    
    return ratio

def clamp(value: int, low: int, high: int) -> int:
    """Simple clamp utility to ensure we stay in range."""
    return max(low, min(high, value))

def create_diff_from_hunks(hunks, file_path):
    """Create a unified diff from a list of hunks."""
    diff_lines = []
    diff_lines.append(f"diff --git a/{file_path} b/{file_path}")
    diff_lines.append(f"--- a/{file_path}")
    diff_lines.append(f"+++ b/{file_path}")
    
    for hunk in hunks:
        # Create the hunk header
        header = f"@@ -{hunk['old_start']},{len(hunk['old_block'])} +{hunk['new_start']},{len(hunk['new_lines'])} @@"
        diff_lines.append(header)
        
        # Add the hunk content
        for line in hunk['old_block']:
            diff_lines.append(f"-{line}")
        for line in hunk['new_lines']:
            diff_lines.append(f"+{line}")
    
    return "\n".join(diff_lines)
