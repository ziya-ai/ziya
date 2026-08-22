"""
Specialized handler for whitespace changes in diffs.

This module provides functions for detecting and handling whitespace-only changes
in diffs, improving the robustness of the diff application pipeline.
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple

# Configure logging
logger = logging.getLogger(__name__)

def is_whitespace_only_diff(hunk: Any) -> bool:
    """Return whether a parsed hunk or unified diff changes only whitespace."""
    removed_lines = []
    added_lines = []

    if isinstance(hunk, str):
        for line in hunk.splitlines():
            if line.startswith(('diff ', 'index ', '--- ', '+++ ', '@@')):
                continue
            if line.startswith('-'):
                removed_lines.append(line[1:])
            elif line.startswith('+'):
                added_lines.append(line[1:])
    elif isinstance(hunk, dict):
        for line in hunk.get('old_block', []):
            if line.startswith('-'):
                removed_lines.append(line[1:])
        for line in hunk.get('new_block', []):
            if line.startswith('+'):
                added_lines.append(line[1:])

    if not removed_lines and not added_lines:
        return False

    non_empty_removed = [line for line in removed_lines if line.strip()]
    non_empty_added = [line for line in added_lines if line.strip()]
    if len(non_empty_removed) != len(non_empty_added):
        return False

    return all(
        re.sub(r'\s+', '', removed) == re.sub(r'\s+', '', added)
        for removed, added in zip(non_empty_removed, non_empty_added)
    )


def normalize_whitespace_for_comparison(
    text: str,
    preserve_indentation: bool = True,
) -> str:
    """Normalize whitespace while optionally retaining leading indentation."""
    normalized = text.replace('\t', '    ')
    normalized = normalized.replace('\r\n', '\n').replace('\r', '\n')

    lines = []
    for line in normalized.split('\n'):
        content = line.lstrip(' ')
        indentation = line[:len(line) - len(content)] if preserve_indentation else ''
        if not content:
            lines.append(indentation)
            continue
        lines.append(indentation + re.sub(r' +', ' ', content).strip())
    return '\n'.join(lines)

def compare_ignoring_whitespace(text1: str, text2: str) -> bool:
    """
    Compare two text strings ignoring whitespace differences.
    
    Args:
        text1: First text to compare
        text2: Second text to compare
        
    Returns:
        True if the texts are equivalent ignoring whitespace, False otherwise
    """
    # Remove all whitespace and compare
    text1_no_ws = re.sub(r'\s+', '', text1)
    text2_no_ws = re.sub(r'\s+', '', text2)
    
    return text1_no_ws == text2_no_ws
