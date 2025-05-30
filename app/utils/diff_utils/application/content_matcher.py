"""
Utilities for matching content in diffs.
"""

import re
from typing import List, Tuple, Optional

from app.utils.logging_utils import logger
from ..core.utils import calculate_block_similarity

def find_best_content_match(file_lines: List[str], chunk_lines: List[str], expected_pos: int) -> Tuple[int, float]:
    """
    Find the best position for a chunk based on content similarity.
    
    Args:
        file_lines: The lines of the file
        chunk_lines: The lines of the chunk
        expected_pos: The expected position
        
    Returns:
        Tuple of (best position, similarity ratio)
    """
    # Extract context lines (lines that start with space)
    context_lines = []
    for line in chunk_lines:
        if line.startswith(' '):
            context_lines.append(line[1:])
        elif not line.startswith('-') and not line.startswith('+'):
            context_lines.append(line)
    
    if not context_lines:
        return expected_pos, 0.0
    
    # Look for the context in the file
    context_block = '\n'.join(context_lines)
    
    # Search in a window around the expected position
    search_start = max(0, expected_pos - 20)
    search_end = min(len(file_lines), expected_pos + 20)

    best_match = expected_pos
    best_ratio = 0.0
    
    for i in range(search_start, search_end):
        if i + len(context_lines) <= len(file_lines):
            window = '\n'.join(file_lines[i:i + len(context_lines)])
            ratio = calculate_block_similarity(window, context_block)

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = i
    
    return best_match, best_ratio

def is_content_already_present(file_lines: List[str], new_lines: List[str]) -> bool:
    """
    Check if the new content is already present in the file.
    
    Args:
        file_lines: The lines of the file
        new_lines: The new lines to check
        
    Returns:
        True if the content is already present, False otherwise
    """
    if not new_lines:
        return True
    
    # Check for exact match
    new_content = '\n'.join(new_lines)
    file_content = '\n'.join(file_lines) if file_lines else ""
    
    if new_content in file_content:
        return True
    
    # Check for line-by-line match
    for i in range(len(file_lines) - len(new_lines) + 1):
        match = True
        for j, new_line in enumerate(new_lines):
            if i + j >= len(file_lines) or file_lines[i + j].rstrip() != new_line.rstrip():
                match = False
                break
        if match:
            return True
    
    return False

def extract_function_name(line: str) -> Optional[str]:
    """
    Extract function name from a line of code.
    
    Args:
        line: The line to check
        
    Returns:
        The function name if found, None otherwise
    """
    # Match function definitions in Python
    match = re.match(r'^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', line)
    if match:
        return match.group(1)
    
    # Match function definitions in JavaScript/TypeScript
    match = re.match(r'^\s*(async\s+)?function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', line)
    if match:
        return match.group(2)
    
    # Match arrow functions with explicit names
    match = re.match(r'^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(?:async\s+)?\(', line)
    if match:
        return match.group(1)
    
    return None
