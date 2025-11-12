"""
Utilities for handling diff hunks.
"""

from typing import List, Dict, Any, Tuple
import re
from app.utils.logging_utils import logger
from ..core.utils import calculate_block_similarity
from ..core.unicode_handling import normalize_unicode
from ..core.escape_handling import normalize_escape_sequences

def fix_hunk_context(lines: List[str]) -> List[str]:
    """
    Fix hunk headers to ensure proper context.
    
    Args:
        lines: The lines of the diff
        
    Returns:
        The lines with fixed hunk headers
    """
    result = []
    in_hunk = False
    hunk_lines = []
    
    for line in lines:
        if line.startswith('@@'):
            if in_hunk and hunk_lines:
                # Process previous hunk
                result.extend(fix_single_hunk_header(hunk_lines))
                hunk_lines = []
            in_hunk = True
            hunk_lines = [line]
        elif in_hunk:
            hunk_lines.append(line)
        else:
            result.append(line)
    
    # Process last hunk
    if in_hunk and hunk_lines:
        result.extend(fix_single_hunk_header(hunk_lines))
    
    return result

def fix_single_hunk_header(hunk_lines: List[str]) -> List[str]:
    """
    Fix a single hunk header based on actual content.
    
    Args:
        hunk_lines: The lines of the hunk
        
    Returns:
        The hunk lines with fixed header
    """
    if not hunk_lines:
        return []
        
    header = hunk_lines[0]
    content = hunk_lines[1:]
    
    # Count actual changes
    old_count = 0
    new_count = 0
    
    for line in content:
        if line.startswith(' '):
            old_count += 1
            new_count += 1
        elif line.startswith('-'):
            old_count += 1
        elif line.startswith('+'):
            new_count += 1
    
    # Extract original line numbers
    match = re.match(r'^@@ -(\d+),\d+ \+(\d+),\d+ @@', header)
    if match:
        old_start = int(match.group(1))
        new_start = int(match.group(2))
        # Create new header with correct counts
        new_header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@"
        return [new_header] + content
    
    return hunk_lines

def normalize_line_for_comparison(line: str) -> str:
    """
    Normalize a line for comparison, handling whitespace, invisible characters, and escape sequences.
    
    Args:
        line: The line to normalize
        
    Returns:
        The normalized line
    """
    if not line:
        return ""
    
    # Remove invisible Unicode characters
    result = normalize_unicode(line)
    
    # Normalize escape sequences
    result = normalize_escape_sequences(result)
    
    # Normalize whitespace
    result = result.strip()
    
    return result

def fix_line_calculation(file_lines: List[str], hunk: Dict[str, Any], pos: int) -> List[str]:
    """
    Fix line calculation issues when applying a hunk.
    
    Args:
        file_lines: List of lines from the file
        hunk: Dictionary containing hunk information
        pos: Position to apply the hunk
        
    Returns:
        The modified lines
    """
    result = file_lines.copy()
    
    # Extract the old and new lines
    old_lines = []
    new_lines = []
    
    for line in hunk.get('old_block', []):
        if line.startswith(' '):
            old_lines.append(line[1:])
        elif line.startswith('-'):
            old_lines.append(line[1:])
    
    for line in hunk.get('old_block', []):
        if line.startswith(' '):
            new_lines.append(line[1:])
        elif line.startswith('+'):
            new_lines.append(line[1:])
    
    # Ensure pos is within bounds
    pos = max(0, min(pos, len(result)))
    
    # Calculate the end position with proper bounds checking
    end_pos = min(pos + len(old_lines), len(result))
    
    # Replace the old lines with the new ones
    result[pos:end_pos] = new_lines
    
    return result
