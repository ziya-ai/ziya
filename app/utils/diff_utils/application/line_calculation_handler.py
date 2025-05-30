"""
Module for handling line calculation issues in diffs.
"""

from typing import List, Dict, Any, Optional, Tuple
from app.utils.logging_utils import logger

def fix_line_calculation(file_path: str, diff_content: str, original_lines: List[str]) -> Optional[List[str]]:
    """
    Generic handler for line calculation fixes.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        original_lines: The original file content as a list of lines
        
    Returns:
        The modified file content as a list of lines, or None if no changes were made
    """
    import re
    
    # Look for common line calculation patterns that need fixing
    modified_lines = []
    for i, line in enumerate(original_lines):
        line_content = line.rstrip('\n')
        
        # Fix 1: Add bounds checking to end_remove calculations
        if re.search(r'end_remove\s*=\s*\w+\s*\+\s*\w+', line_content):
            # This is an end_remove calculation without bounds checking
            # Extract the variable names
            match = re.search(r'end_remove\s*=\s*(\w+)\s*\+\s*(\w+)', line_content)
            if match:
                var1, var2 = match.groups()
                # Look for array/list variables in context
                array_vars = set()
                for j in range(max(0, i-5), min(len(original_lines), i+5)):
                    array_match = re.search(r'(\w+)\s*=\s*len\((\w+)\)', original_lines[j])
                    if array_match:
                        array_vars.add(array_match.group(2))
                
                # If we found array variables, use the first one for bounds checking
                if array_vars:
                    array_var = next(iter(array_vars))
                    # Replace with bounds-checked version
                    indent = re.match(r'^(\s*)', line_content).group(1)
                    new_line = f"{indent}end_remove = min({var1} + {var2}, len({array_var}))"
                    if line.endswith('\n'):
                        new_line += '\n'
                    modified_lines.append(new_line)
                    continue
        
        # Fix 2: Fix available_lines calculations to use the right array
        if re.search(r'available_lines\s*=\s*len\((\w+)\)\s*-\s*(\w+)', line_content):
            match = re.search(r'available_lines\s*=\s*len\((\w+)\)\s*-\s*(\w+)', line_content)
            if match:
                array_var, pos_var = match.groups()
                
                # Look for other array variables that might be more appropriate
                array_vars = set()
                for j in range(max(0, i-10), min(len(original_lines), i+10)):
                    # Look for function parameters that might be the original array
                    param_match = re.search(r'def\s+\w+\(([^)]+)\)', original_lines[j])
                    if param_match:
                        params = param_match.group(1).split(',')
                        for param in params:
                            param = param.strip()
                            if param and param != array_var and "stripped" in param:
                                array_vars.add(param)
                
                # If we found a better array variable, use it
                if array_vars:
                    better_array = next(iter(array_vars))
                    indent = re.match(r'^(\s*)', line_content).group(1)
                    new_line = f"{indent}available_lines = len({better_array}) - {pos_var}"
                    if line.endswith('\n'):
                        new_line += '\n'
                    modified_lines.append(new_line)
                    continue
        
        # Keep other lines unchanged
        modified_lines.append(line)
    
    # Only return modified lines if we actually made changes
    if ''.join(modified_lines) != ''.join(original_lines):
        return modified_lines
    
    return None
