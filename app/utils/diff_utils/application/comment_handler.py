"""
Comment handling utilities for diff application.

This module provides specialized handling for comment-only changes in diffs,
allowing matches even when only comments differ.
"""

import re
from typing import List, Tuple, Dict, Any, Optional
import logging

# Configure logging
logger = logging.getLogger(__name__)

def is_comment_line(line: str, language: str = None) -> bool:
    """
    Detect if a line is a comment based on language-specific patterns.
    
    Args:
        line: The line to check
        language: Optional language hint (python, javascript, etc.)
        
    Returns:
        True if the line is a comment, False otherwise
    """
    line = line.strip()
    if not line:
        return False
    
    # Detect language if not provided
    if not language:
        if line.startswith('#'):
            language = 'python'
        elif line.startswith('//') or line.startswith('/*'):
            language = 'c_family'
        elif line.startswith('--'):
            language = 'sql'
        elif line.startswith('<!--'):
            language = 'html'
        
    # Check language-specific patterns
    if language == 'python':
        return line.startswith('#')
    elif language in ('javascript', 'java', 'c', 'cpp', 'c_family'):
        return line.startswith('//') or line.startswith('/*') or line.endswith('*/')
    elif language == 'html':
        return line.startswith('<!--') or line.endswith('-->')
    elif language == 'sql':
        return line.startswith('--')
    
    # Generic detection for unknown languages
    comment_patterns = ['#', '//', '/*', '*/', '--', '<!--', '-->']
    return any(line.startswith(pattern) for pattern in comment_patterns)

def detect_file_language(file_path: str) -> str:
    """
    Detect the programming language of a file based on its extension.
    
    Args:
        file_path: Path to the file
        
    Returns:
        The detected language or None if unknown
    """
    if file_path.endswith('.py'):
        return 'python'
    elif file_path.endswith(('.js', '.ts', '.jsx', '.tsx')):
        return 'javascript'
    elif file_path.endswith(('.java')):
        return 'java'
    elif file_path.endswith(('.c', '.cpp', '.h', '.hpp')):
        return 'cpp'
    elif file_path.endswith(('.html', '.htm', '.xml')):
        return 'html'
    elif file_path.endswith(('.sql')):
        return 'sql'
    elif file_path.endswith(('.md', '.markdown')):
        return 'markdown'
    elif file_path.endswith(('.sh', '.bash')):
        return 'shell'
    elif file_path.endswith(('.css')):
        return 'css'
    
    return None

def is_comment_only_change(file_slice: List[str], chunk_lines: List[str], language: str = None) -> bool:
    """
    Check if the difference between file_slice and chunk_lines is only in comments.
    
    Args:
        file_slice: A slice of the file content
        chunk_lines: The chunk to compare against
        language: Optional language hint
        
    Returns:
        True if the only differences are in comments, False otherwise
    """
    if len(file_slice) != len(chunk_lines):
        return False
    
    # Check each line pair
    for file_line, chunk_line in zip(file_slice, chunk_lines):
        # If both are comments, continue
        if is_comment_line(file_line, language) and is_comment_line(chunk_line, language):
            continue
        
        # If one is a comment and the other isn't, it's not a comment-only change
        if is_comment_line(file_line, language) != is_comment_line(chunk_line, language):
            return False
        
        # If neither is a comment, compare the non-comment content
        if not is_comment_line(file_line, language) and not is_comment_line(chunk_line, language):
            # Remove any trailing comments
            file_code = remove_trailing_comment(file_line, language)
            chunk_code = remove_trailing_comment(chunk_line, language)
            
            # Compare the code parts (ignoring whitespace)
            if file_code.strip() != chunk_code.strip():
                return False
    
    return True

def remove_trailing_comment(line: str, language: str = None) -> str:
    """
    Remove trailing comments from a line of code.
    
    Args:
        line: The line to process
        language: Optional language hint
        
    Returns:
        The line with trailing comments removed
    """
    if not language:
        # Try to detect language from the line
        if '#' in line:
            language = 'python'
        elif '//' in line:
            language = 'c_family'
        elif '--' in line:
            language = 'sql'
    
    if language == 'python':
        # Handle Python comments
        parts = line.split('#', 1)
        return parts[0]
    elif language in ('javascript', 'java', 'c', 'cpp', 'c_family'):
        # Handle C-style comments
        parts = line.split('//', 1)
        return parts[0]
    elif language == 'sql':
        # Handle SQL comments
        parts = line.split('--', 1)
        return parts[0]
    
    # Generic approach for unknown languages
    for pattern in ['#', '//', '--']:
        if pattern in line:
            parts = line.split(pattern, 1)
            return parts[0]
    
    return line

def calculate_match_quality_with_comment_awareness(file_slice: List[str], chunk_lines: List[str], language: str = None) -> float:
    """
    Calculate match quality with special handling for comment lines.
    
    Args:
        file_slice: A slice of the file content
        chunk_lines: The chunk to compare against
        language: Optional language hint
        
    Returns:
        A quality score between 0.0 and 1.0
    """
    if not chunk_lines:
        return 1.0
    
    match_count = 0
    comment_lines = 0
    
    for i, chunk_line in enumerate(chunk_lines):
        if i < len(file_slice):
            # Check if either line is a comment
            chunk_is_comment = is_comment_line(chunk_line, language)
            file_is_comment = is_comment_line(file_slice[i], language)
            
            if chunk_is_comment or file_is_comment:
                comment_lines += 1
                # Give partial credit for comment lines
                match_count += 0.5
            elif file_slice[i].strip() == chunk_line.strip():
                # Full credit for exact matches
                match_count += 1
            else:
                # Try token-based comparison for non-comment lines
                file_code = remove_trailing_comment(file_slice[i], language)
                chunk_code = remove_trailing_comment(chunk_line, language)
                
                if file_code.strip() == chunk_code.strip():
                    # The code parts match after removing comments
                    match_count += 0.9
                else:
                    # Try token-based comparison
                    file_tokens = set(re.findall(r'\w+', file_code))
                    chunk_tokens = set(re.findall(r'\w+', chunk_code))
                    
                    if file_tokens and chunk_tokens:
                        # Calculate Jaccard similarity
                        intersection = len(file_tokens.intersection(chunk_tokens))
                        union = len(file_tokens.union(chunk_tokens))
                        
                        if intersection / union >= 0.7:
                            match_count += 0.7
    
    # If all differences are in comments, boost the score
    if comment_lines > 0 and match_count >= (len(chunk_lines) - comment_lines):
        return 0.9  # High confidence for comment-only differences
    
    return match_count / len(chunk_lines)

def handle_comment_only_changes(file_path: str, file_lines: List[str], chunk_lines: List[str], expected_pos: int) -> Tuple[Optional[int], float]:
    """
    Specialized handler for comment-only changes.
    
    Args:
        file_path: Path to the file
        file_lines: The file content as a list of lines
        chunk_lines: The chunk to find in the file
        expected_pos: The expected position of the chunk
        
    Returns:
        Tuple of (best_position, confidence_ratio)
    """
    language = detect_file_language(file_path)
    logger.debug(f"Detected language for {file_path}: {language}")
    
    # Get search radius
    search_radius = 50  # Use a reasonable default
    
    # Calculate search range
    start_pos = max(0, expected_pos - search_radius) if expected_pos is not None else 0
    end_pos = min(len(file_lines), expected_pos + search_radius) if expected_pos is not None else len(file_lines)
    
    best_pos = expected_pos
    best_ratio = 0.0
    
    # Search for the best match
    for pos in range(start_pos, end_pos):
        if pos + len(chunk_lines) > len(file_lines):
            continue
        
        file_slice = file_lines[pos:pos + len(chunk_lines)]
        
        # Check if this is a comment-only change
        if is_comment_only_change(file_slice, chunk_lines, language):
            logger.debug(f"Found comment-only change at position {pos}")
            return pos, 0.95  # High confidence for comment-only changes
        
        # Calculate match quality with comment awareness
        ratio = calculate_match_quality_with_comment_awareness(file_slice, chunk_lines, language)
        
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = pos
    
    # If we found a good match
    if best_ratio >= 0.8:
        logger.debug(f"Found good match with comment awareness at position {best_pos} with ratio {best_ratio:.4f}")
        return best_pos, best_ratio
    
    # If we found a decent match and it's close to the expected position
    if best_ratio >= 0.6 and abs(best_pos - expected_pos) <= 10:
        logger.debug(f"Found decent match near expected position at {best_pos} with ratio {best_ratio:.4f}")
        return best_pos, best_ratio
    
    # No good match found
    return None, best_ratio
