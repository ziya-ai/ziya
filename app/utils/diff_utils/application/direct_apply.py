"""
Generic application of fixes for common issues.
"""

import os
import re
from app.utils.logging_utils import logger

def apply_line_calculation_fix(file_path: str) -> bool:
    """
    Apply line calculation fixes using a generic approach.
    
    Args:
        file_path: Path to the file to modify
        
    Returns:
        True if the fix was applied, False otherwise
    """
    try:
        logger.info(f"Using generic line calculation fix for {file_path}")
        
        # Read the original file content
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
            original_lines = original_content.splitlines(True)
        
        # Apply generic fixes for common line calculation issues
        modified_lines = []
        for line in original_lines:
            # Fix 1: Add bounds checking to end_remove calculations
            if re.search(r'end_remove\s*=\s*\w+\s*\+\s*\w+', line):
                # Extract the variable names
                match = re.search(r'end_remove\s*=\s*(\w+)\s*\+\s*(\w+)', line)
                if match:
                    var1, var2 = match.groups()
                    # Find array variables in the content
                    array_vars = re.findall(r'len\((\w+)\)', original_content)
                    if array_vars:
                        # Use the most common array variable
                        from collections import Counter
                        array_var = Counter(array_vars).most_common(1)[0][0]
                        # Replace with bounds-checked version
                        indent = re.match(r'^(\s*)', line).group(1)
                        modified_line = f"{indent}end_remove = min({var1} + {var2}, len({array_var}))"
                        if line.endswith('\n'):
                            modified_line += '\n'
                        modified_lines.append(modified_line)
                        continue
            
            # Fix 2: Use consistent array variables for available_lines calculations
            if re.search(r'available_lines\s*=\s*len\((\w+)\)\s*-\s*(\w+)', line):
                match = re.search(r'available_lines\s*=\s*len\((\w+)\)\s*-\s*(\w+)', line)
                if match:
                    array_var, pos_var = match.groups()
                    # Look for function parameters
                    func_match = re.search(r'def\s+\w+\(([^)]+)\)', original_content)
                    if func_match:
                        params = func_match.group(1).split(',')
                        # Find parameter that looks like original data
                        for param in params:
                            param = param.strip()
                            if param and param != array_var and ('original' in param or 'stripped' in param):
                                # Replace with the better variable
                                indent = re.match(r'^(\s*)', line).group(1)
                                modified_line = f"{indent}available_lines = len({param}) - {pos_var}"
                                if line.endswith('\n'):
                                    modified_line += '\n'
                                modified_lines.append(modified_line)
                                break
                        else:
                            # No better variable found, keep original
                            modified_lines.append(line)
                        continue
            
            # Keep other lines unchanged
            modified_lines.append(line)
        
        # Only write if we made changes
        if modified_lines and ''.join(modified_lines) != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(''.join(modified_lines))
            
            logger.info(f"Successfully applied line calculation fixes to {file_path}")
            return True
        else:
            logger.info(f"No line calculation fixes needed for {file_path}")
            return False
    except Exception as e:
        logger.error(f"Error applying line calculation fix: {str(e)}")
        return False
