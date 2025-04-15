"""
Context extraction utilities for diff application.

This module provides functions for extracting context around positions in text,
which is useful for diff application and verification.
"""

from typing import List, Dict, Any, Optional
from ..core.config import get_context_size

def extract_context(content: str, position: int, context_size: Optional[int] = None, size_category: str = 'medium') -> str:
    """
    Extract context around a position in the content.
    
    Args:
        content: The content
        position: The position
        context_size: The size of context to extract (if None, use configured size)
        size_category: The context size category ('small', 'medium', 'large', 'full')
        
    Returns:
        The extracted context
    """
    if context_size is None:
        context_size = get_context_size(size_category)
        
    start = max(0, position - context_size // 2)
    end = min(len(content), position + context_size // 2)
    return content[start:end]

def extract_line_context(lines: List[str], line_number: int, context_lines: Optional[int] = None, size_category: str = 'medium') -> List[str]:
    """
    Extract context lines around a specific line number.
    
    Args:
        lines: The lines of content
        line_number: The line number (0-based)
        context_lines: The number of context lines to extract (if None, use configured size)
        size_category: The context size category ('small', 'medium', 'large', 'full')
        
    Returns:
        The extracted context lines
    """
    if context_lines is None:
        context_lines = get_context_size(size_category) // 20  # Approximate number of lines
        
    start = max(0, line_number - context_lines)
    end = min(len(lines), line_number + context_lines + 1)
    return lines[start:end]

def extract_hunk_context(hunk: Dict[str, Any], context_lines: Optional[int] = None) -> Dict[str, Any]:
    """
    Extract context from a hunk for better matching.
    
    Args:
        hunk: The hunk to extract context from
        context_lines: The number of context lines to extract (if None, use adaptive sizing)
        
    Returns:
        A new hunk with extracted context
    """
    from ..core.config import calculate_adaptive_context_size
    
    if context_lines is None:
        # Calculate adaptive context size based on hunk size
        old_block = hunk.get('old_block', [])
        hunk_size = len(old_block)
        context_lines = calculate_adaptive_context_size(hunk_size)
    
    # Extract context from the beginning and end of the hunk
    old_block = hunk.get('old_block', [])
    if len(old_block) <= context_lines * 2:
        # If the hunk is small, use the whole hunk
        context_old_block = old_block
    else:
        # Otherwise, extract context from beginning and end
        context_old_block = old_block[:context_lines] + old_block[-context_lines:]
    
    # Create a new hunk with the extracted context
    context_hunk = hunk.copy()
    context_hunk['old_block'] = context_old_block
    
    return context_hunk
