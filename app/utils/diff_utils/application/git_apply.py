"""
Module for applying diffs using git apply.
"""

import os
import subprocess
import tempfile
from typing import Dict, List, Any, Optional, Tuple
from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError

def apply_diff_with_git(git_diff: str, codebase_dir: str, file_path: str) -> bool:
    """
    Apply a diff using git apply.
    
    Args:
        git_diff: The git diff to apply
        codebase_dir: The base directory of the codebase
        file_path: Path to the file to modify
        
    Returns:
        True if the diff was applied successfully, False otherwise
    """
    # Create a temporary file for the diff
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.diff', delete=False) as temp_file:
            temp_file.write(git_diff)
            temp_path = temp_file.name
        
        # Check if this is a missing newline at end of file issue
        if "\\ No newline at end of file" in git_diff:
            # Read the original file content
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    original_content = f.read()
            except FileNotFoundError:
                original_content = ""
            
            # Process with the newline handler
            from .newline_handler import process_newline_changes
            modified_content = process_newline_changes(original_content, git_diff)
            
            # Write the modified content back to the file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(modified_content)
            
            return True
        
        # Apply the diff with git apply
        git_result = subprocess.run(
            ['git', 'apply', '--verbose', '--ignore-whitespace',
             '--ignore-space-change', '--whitespace=nowarn',
             temp_path],
            cwd=codebase_dir,
            capture_output=True,
            text=True
        )
        
        logger.debug(f"Git apply stdout: {git_result.stdout}")
        logger.debug(f"Git apply stderr: {git_result.stderr}")
        logger.debug(f"Git apply return code: {git_result.returncode}")
        
        # Check if the diff was applied successfully
        if git_result.returncode == 0:
            logger.info("Git apply succeeded")
            return True
        else:
            logger.warning(f"Git apply failed: {git_result.stderr}")
            return False
        
    except Exception as e:
        logger.error(f"Error applying diff with git: {str(e)}")
        return False
    finally:
        # Clean up the temporary file
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)

def parse_git_apply_output(output: str) -> Dict[int, bool]:
    """
    Parse the output of git apply to determine which hunks succeeded.
    
    Args:
        output: The output of git apply
        
    Returns:
        A dictionary mapping hunk IDs to success status
    """
    result = {}
    
    # Parse the output to determine which hunks succeeded
    lines = output.splitlines()
    current_hunk = None
    
    for line in lines:
        # Look for hunk headers
        if line.startswith('Hunk #'):
            # Extract the hunk number
            try:
                current_hunk = int(line.split('#')[1].split(' ')[0])
                result[current_hunk] = False
            except (IndexError, ValueError):
                pass
        
        # Look for success indicators
        if current_hunk is not None and 'succeeded' in line.lower():
            result[current_hunk] = True
        
        # Look for failure indicators
            if current_hunk is not None and 'failed' in line.lower():
                result[current_hunk] = False
    
        return result
