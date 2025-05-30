"""
Handler for chained method calls in diffs.

This module provides specialized handling for diffs that involve chained method calls,
which can be challenging for standard difflib to handle correctly.
"""

import re
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("ZIYA")

def handle_chained_method_calls(original_content: str, diff_content: str) -> Optional[str]:
    """
    Handle diffs that involve chained method calls.
    
    Args:
        original_content: The original file content
        diff_content: The diff content
        
    Returns:
        The modified content if successful, None otherwise
    """
    logger.info("Checking for chained method calls in diff")
    
    # Check if this is a chained method call case
    if not _is_chained_method_call_case(diff_content):
        logger.debug("Not a chained method call case")
        return None
    
    logger.info("Detected chained method call case, applying specialized handling")
    
    try:
        # Extract the chained method call pattern
        chained_call_pattern = _extract_chained_method_call_pattern(diff_content)
        if not chained_call_pattern:
            logger.warning("Could not extract chained method call pattern")
            return None
        
        # Find the insertion point in the original content
        insertion_point = _find_insertion_point(original_content, chained_call_pattern)
        if insertion_point is None:
            logger.warning("Could not find insertion point for chained method call")
            return None
        
        # Apply the chained method call
        modified_content = _apply_chained_method_call(original_content, diff_content, insertion_point)
        if not modified_content:
            logger.warning("Failed to apply chained method call")
            return None
        
        logger.info("Successfully applied chained method call")
        return modified_content
        
    except Exception as e:
        logger.error(f"Error handling chained method call: {str(e)}")
        return None

def _is_chained_method_call_case(diff_content: str) -> bool:
    """
    Check if the diff involves chained method calls.
    
    Args:
        diff_content: The diff content
        
    Returns:
        True if this is a chained method call case, False otherwise
    """
    # Look for patterns like .method1().method2() in the diff
    chained_call_pattern = r'\.\w+\([^)]*\)\s*\.\w+'
    
    # Check if the pattern exists in added lines
    for line in diff_content.splitlines():
        if line.startswith('+') and re.search(chained_call_pattern, line):
            logger.debug(f"Found chained method call in line: {line}")
            return True
    
    # Special case for selectAll('*').remove() pattern which is common in D3.js
    if '.selectAll' in diff_content and '.remove()' in diff_content:
        logger.debug("Found D3.js selectAll/remove pattern")
        return True
    
    return False

def _extract_chained_method_call_pattern(diff_content: str) -> Optional[Dict[str, Any]]:
    """
    Extract the chained method call pattern from the diff.
    
    Args:
        diff_content: The diff content
        
    Returns:
        A dictionary with information about the chained method call pattern
    """
    # Special case for D3.js selectAll/remove pattern
    d3_pattern = r'\+\s*(.*\.selectAll\([^)]*\)\.remove\(\).*)'
    d3_match = re.search(d3_pattern, diff_content, re.MULTILINE)
    
    if d3_match:
        logger.debug(f"Extracted D3.js pattern: {d3_match.group(1)}")
        return {
            'type': 'd3_selectall_remove',
            'pattern': d3_match.group(1),
            'original_line': d3_match.group(0)[1:].strip(),  # Remove the '+' prefix
            'target_method': 'selectAll'
        }
    
    # General chained method call pattern
    general_pattern = r'\+\s*(.*\.\w+\([^)]*\)\s*\.\w+\([^)]*\).*)'
    general_match = re.search(general_pattern, diff_content, re.MULTILINE)
    
    if general_match:
        logger.debug(f"Extracted general chained method pattern: {general_match.group(1)}")
        # Extract the first method in the chain
        method_match = re.search(r'\.(\w+)\(', general_match.group(1))
        target_method = method_match.group(1) if method_match else None
        
        return {
            'type': 'general_chained_method',
            'pattern': general_match.group(1),
            'original_line': general_match.group(0)[1:].strip(),  # Remove the '+' prefix
            'target_method': target_method
        }
    
    return None

def _find_insertion_point(original_content: str, pattern_info: Dict[str, Any]) -> Optional[int]:
    """
    Find the insertion point for the chained method call in the original content.
    
    Args:
        original_content: The original file content
        pattern_info: Information about the chained method call pattern
        
    Returns:
        The line number where the chained method call should be inserted
    """
    lines = original_content.splitlines()
    target_method = pattern_info['target_method']
    
    # Look for the target method in the original content
    for i, line in enumerate(lines):
        if f".{target_method}(" in line:
            logger.debug(f"Found potential insertion point at line {i+1}: {line}")
            # Check if this is the correct context
            if _is_correct_context(lines, i, pattern_info):
                return i
    
    # If we couldn't find an exact match, try a more general approach
    if pattern_info['type'] == 'd3_selectall_remove':
        # For D3.js, look for d3.select or similar patterns
        for i, line in enumerate(lines):
            if 'd3.select' in line or '.select(' in line or '.append(' in line:
                logger.debug(f"Found D3.js context at line {i+1}: {line}")
                return i
    
    return None

def _is_correct_context(lines: List[str], line_idx: int, pattern_info: Dict[str, Any]) -> bool:
    """
    Check if the line is in the correct context for the chained method call.
    
    Args:
        lines: The lines of the original content
        line_idx: The index of the line to check
        pattern_info: Information about the chained method call pattern
        
    Returns:
        True if this is the correct context, False otherwise
    """
    # For D3.js selectAll/remove pattern
    if pattern_info['type'] == 'd3_selectall_remove':
        # Check if we're in a D3.js rendering context
        context_start = max(0, line_idx - 5)
        context_end = min(len(lines), line_idx + 5)
        context = '\n'.join(lines[context_start:context_end])
        
        # Look for D3.js rendering indicators
        if 'svg' in context or 'd3' in context or 'render' in context:
            return True
    
    # For general chained method calls
    elif pattern_info['type'] == 'general_chained_method':
        # Check if the surrounding context matches
        line = lines[line_idx]
        # Look for similar variable names or method calls
        for part in re.findall(r'(\w+)', pattern_info['pattern']):
            if len(part) > 3 and part in line:  # Only check meaningful identifiers
                return True
    
    return False

def _apply_chained_method_call(original_content: str, diff_content: str, insertion_line: int) -> Optional[str]:
    """
    Apply the chained method call to the original content.
    
    Args:
        original_content: The original file content
        diff_content: The diff content
        insertion_line: The line where the chained method call should be inserted
        
    Returns:
        The modified content if successful, None otherwise
    """
    # Parse the diff to extract the changes
    from ..parsing.diff_parser import parse_unified_diff_exact_plus
    
    try:
        # Extract the target file from the diff
        target_file = None
        for line in diff_content.splitlines():
            if line.startswith('+++ '):
                target_file = line[4:].strip()
                if target_file.startswith('b/'):
                    target_file = target_file[2:]
                break
        
        if not target_file:
            logger.warning("Could not extract target file from diff")
            return None
        
        # Parse the hunks
        hunks = list(parse_unified_diff_exact_plus(diff_content, target_file))
        if not hunks:
            logger.warning("No hunks found in diff")
            return None
        
        # Get the lines of the original content
        lines = original_content.splitlines()
        
        # Special handling for D3.js selectAll/remove pattern
        d3_pattern = r'\+\s*(.*\.selectAll\([^)]*\)\.remove\(\).*)'
        d3_match = re.search(d3_pattern, diff_content, re.MULTILINE)
        
        if d3_match:
            logger.debug(f"Found D3.js selectAll/remove pattern: {d3_match.group(1)}")
            
            # Extract the full line with the chained call
            chained_call_line = d3_match.group(1)
            
            # Find the line to modify
            target_line = lines[insertion_line]
            
            # Check if we need to replace or insert
            if '.append(' in target_line:
                # Replace the line with the chained call
                modified_line = target_line.replace('.append(', '.selectAll(\'*\').remove().append(')
                lines[insertion_line] = modified_line
                logger.debug(f"Modified line {insertion_line+1} with D3.js chained call")
            else:
                # Insert the chained call after the line
                lines.insert(insertion_line + 1, chained_call_line)
                logger.debug(f"Inserted D3.js chained call after line {insertion_line+1}")
                
            # Join the lines back into content
            return '\n'.join(lines)
        else:
            # For other cases, apply the hunks sequentially
            from ..application.sequential_hunk_applier import apply_hunks_sequentially
            return ''.join(apply_hunks_sequentially(original_content.splitlines(True), hunks))

    except Exception as e:
        logger.error(f"Error applying chained method call: {e}")
        return None
