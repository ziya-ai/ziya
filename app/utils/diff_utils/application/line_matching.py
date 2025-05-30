from typing import List
from app.utils.logging_utils import logger
from app.utils.diff_utils.core.unicode_handling import normalize_unicode

def match_lines(file_lines: List[str], hunk_lines: List[str]) -> bool:
    """
    Check if file lines match hunk lines, with normalization.
    
    Args:
        file_lines: Lines from the file
        hunk_lines: Lines from the hunk
        
    Returns:
        True if the lines match, False otherwise
    """
    if len(file_lines) != len(hunk_lines):
        return False
    
    for file_line, hunk_line in zip(file_lines, hunk_lines):
        # Normalize both lines for comparison
        normalized_file = normalize_line(file_line)
        normalized_hunk = normalize_line(hunk_line)
        
        if normalized_file != normalized_hunk:
            # Try a more lenient comparison that ignores all whitespace
            stripped_file = ''.join(normalized_file.split())
            stripped_hunk = ''.join(normalized_hunk.split())
            
            if stripped_file != stripped_hunk:
                return False
    
    return True

def match_normalized_lines(file_lines: List[str], hunk_lines: List[str]) -> bool:
    """
    Check if file lines match hunk lines with more aggressive normalization.
    
    Args:
        file_lines: Lines from the file
        hunk_lines: Lines from the hunk
        
    Returns:
        True if the lines match, False otherwise
    """
    if len(file_lines) != len(hunk_lines):
        return False
    
    for file_line, hunk_line in zip(file_lines, hunk_lines):
        # Normalize both lines for comparison
        normalized_file = normalize_line(file_line)
        normalized_hunk = normalize_line(hunk_line)
        
        # Remove all whitespace
        stripped_file = ''.join(normalized_file.split())
        stripped_hunk = ''.join(normalized_hunk.split())
        
        # Remove common punctuation
        for char in ',.;:()[]{}':
            stripped_file = stripped_file.replace(char, '')
            stripped_hunk = stripped_hunk.replace(char, '')
        
        if stripped_file != stripped_hunk:
            return False
    
    return True

def normalize_line(line: str) -> str:
    """
    Normalize a line for comparison.
    
    Args:
        line: The line to normalize
        
    Returns:
        The normalized line
    """
    # Remove trailing whitespace and line endings
    normalized = line.rstrip('\r\n').rstrip()
    
    # Use the unicode_handling module to handle invisible characters
    return normalize_unicode(normalized)
