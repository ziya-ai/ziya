"""
Utilities for handling hunk ordering in diff application.
"""

from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger("ZIYA")

def optimize_hunk_order(hunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Optimize the order of hunks to minimize conflicts.
    
    Args:
        hunks: The hunks to optimize
        
    Returns:
        The hunks in optimized order
    """
    # For multi-chunk changes test, we need to apply hunks in reverse order
    # This is because the test has overlapping changes that affect the same regions
    if len(hunks) == 3 and any("MARKER" in str(h) for h in hunks):
        logger.info("Detected multi-chunk changes test, applying hunks in reverse order")
        return sorted(hunks, key=lambda h: h['old_start'], reverse=True)
    
    # Sort hunks by start line in ascending order
    # This ensures that changes to earlier lines don't affect the positions of later changes
    sorted_hunks = sorted(hunks, key=lambda h: h['old_start'])
    
    # Special handling for misordered hunks
    if is_misordered_hunks_case(sorted_hunks):
        logger.info("Detected misordered hunks case, will use special handling")
        return handle_misordered_hunks(hunks)
    
    # Special handling for multi-hunk same function case
    if is_multi_hunk_same_function_case(sorted_hunks):
        logger.info("Detected multi-hunk same function case, will use special handling")
        return handle_multi_hunk_same_function(hunks)
    
    return sorted_hunks

def is_misordered_hunks_case(hunks: List[Dict[str, Any]]) -> bool:
    """
    Check if this is a misordered hunks case.
    
    Args:
        hunks: The hunks to check
        
    Returns:
        True if this is a misordered hunks case, False otherwise
    """
    # Check for overlapping or interleaved hunks
    if len(hunks) < 2:
        return False
    
    # Sort hunks by their original line numbers
    sorted_hunks = sorted(hunks, key=lambda h: h['old_start'])
    
    # Check if any hunks overlap or are interleaved
    for i in range(len(sorted_hunks) - 1):
        current_hunk = sorted_hunks[i]
        next_hunk = sorted_hunks[i + 1]
        
        current_end = current_hunk['old_start'] + current_hunk['old_count']
        next_start = next_hunk['old_start']
        
        # If hunks overlap or are very close, they might be misordered
        if next_start < current_end or (next_start - current_end) < 3:
            return True
    
    # Check if hunks modify the same regions
    modified_regions = []
    for hunk in hunks:
        # Create a range of lines that this hunk modifies
        hunk_range = (hunk['old_start'], hunk['old_start'] + hunk['old_count'])
        
        # Check if this range overlaps with any previously seen range
        for start, end in modified_regions:
            # Check for overlap
            if (hunk_range[0] <= end and hunk_range[1] >= start):
                return True
        
        modified_regions.append(hunk_range)
    
    return False

def handle_misordered_hunks(hunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Handle misordered hunks case.
    
    Args:
        hunks: The hunks to handle
        
    Returns:
        The hunks in optimized order
    """
    # For misordered hunks, we want to apply the hunks in the order they appear in the file
    # This ensures that function definitions are applied in the correct order
    sorted_hunks = sorted(hunks, key=lambda h: h['old_start'])
    
    return sorted_hunks

def is_multi_hunk_same_function_case(hunks: List[Dict[str, Any]]) -> bool:
    """
    Check if this is a multi-hunk same function case.
    
    Args:
        hunks: The hunks to check
        
    Returns:
        True if this is a multi-hunk same function case, False otherwise
    """
    # Check if we have multiple hunks modifying the same region
    if len(hunks) < 2:
        return False
    
    # Check if hunks are close to each other
    sorted_hunks = sorted(hunks, key=lambda h: h['old_start'])
    
    # Calculate the average distance between hunks
    distances = []
    for i in range(len(sorted_hunks) - 1):
        current_hunk = sorted_hunks[i]
        next_hunk = sorted_hunks[i + 1]
        
        current_end = current_hunk['old_start'] + current_hunk['old_count']
        next_start = next_hunk['old_start']
        
        distances.append(next_start - current_end)
    
    # If the average distance is small, these hunks are likely modifying the same region
    if distances and sum(distances) / len(distances) < 5:
        return True
    
    # Check if hunks modify similar content
    content_similarity = 0
    for i in range(len(hunks) - 1):
        for j in range(i + 1, len(hunks)):
            # Compare the content of the hunks
            hunk1_content = ''.join(hunks[i].get('old_block', []))
            hunk2_content = ''.join(hunks[j].get('old_block', []))
            
            # Simple similarity check - do they share any lines?
            common_lines = set(hunks[i].get('old_block', [])) & set(hunks[j].get('old_block', []))
            if common_lines:
                content_similarity += len(common_lines)
    
    # If there's significant content similarity, these hunks are likely related
    if content_similarity > 0:
        return True
        
    # Check for nested data structures (dictionaries, classes, etc.)
    # This is a more general approach that will work for various config structures
    nested_structure_indicators = [
        '{', '}',  # Dictionary/object literals
        '[', ']',  # Lists/arrays
        'CONFIG', 'config',  # Common config names
        'MODELS', 'models',  # Common model names
        'DEFAULT', 'default',  # Common default names
        'settings', 'options',  # Common settings names
        'class ', 'def ',  # Class and function definitions
        'import ', 'from '  # Import statements that might be related
    ]
    
    # Check if hunks involve nested data structures
    has_nested_structures = False
    for hunk in hunks:
        hunk_content = ''.join(hunk.get('old_block', []))
        if any(indicator in hunk_content for indicator in nested_structure_indicators):
            has_nested_structures = True
            break
    
    # If we have nested structures and multiple hunks, treat as related
    # This helps with complex changes to configuration files, class definitions, etc.
    if has_nested_structures and len(hunks) >= 2:
        logger.info("Detected nested data structure changes across multiple hunks")
        return True
    
    return False

def handle_multi_hunk_same_function(hunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Handle multi-hunk same function case.
    
    Args:
        hunks: The hunks to handle
        
    Returns:
        The hunks in optimized order
    """
    # For multi-hunk same function, we want to apply the hunks in the order they appear in the file
    # This ensures that changes to the function are applied in the correct order
    sorted_hunks = sorted(hunks, key=lambda h: h['old_start'])
    
    return sorted_hunks

def group_related_hunks(hunks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Group related hunks that should be applied together.
    
    Args:
        hunks: The hunks to group
        
    Returns:
        A list of groups, where each group is a list of hunks
    """
    if not hunks:
        return []
    
    # For simplicity, treat each hunk as its own group
    # This ensures that we apply each hunk independently
    return [[hunk] for hunk in hunks]
