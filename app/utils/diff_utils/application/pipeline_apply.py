"""
Pipeline-based diff application for in-memory content.

This module provides a function to apply diffs using the pipeline approach
with in-memory content, ensuring consistent validation across all code paths.
"""

import os
import tempfile
import re
from typing import Dict, Any, List

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import parse_unified_diff_exact_plus

def validate_diff_hunks(diff_content: str, file_path: str) -> None:
    """
    Validate that all hunks in the diff are well-formed.
    
    Args:
        diff_content: The diff content to validate
        file_path: Path to the file (for error reporting)
        
    Raises:
        PatchApplicationError: If any hunks are malformed
    """
    try:
        hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
        if not hunks:
            raise PatchApplicationError("No valid hunks found in diff", {
                "status": "error",
                "details": {"error": "No valid hunks found in diff"}
            })
        
        malformed_hunks = []
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk.get('number', i)
            
            # Check if the hunk is malformed
            if 'header' in hunk and '@@ -' in hunk['header']:
                header_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', hunk['header'])
                if not header_match:
                    logger.warning(f"Malformed hunk header detected: {hunk['header']}")
                    malformed_hunks.append(hunk_id)
                    continue
            
            # Check if essential hunk data is missing
            if not hunk.get('old_block') or not hunk.get('new_lines'):
                logger.warning(f"Malformed hunk detected: missing old_block or new_lines")
                malformed_hunks.append(hunk_id)
                continue
        
        # If any hunks are malformed, raise an error
        if malformed_hunks:
            logger.warning(f"Found {len(malformed_hunks)} malformed hunks, aborting")
            raise PatchApplicationError(
                "Malformed hunks detected",
                {
                    "status": "error",
                    "type": "malformed_hunks",
                    "details": {"malformed_hunks": malformed_hunks}
                }
            )
    except PatchApplicationError:
        # Re-raise PatchApplicationError
        raise
    except Exception as e:
        # Wrap other exceptions
        raise PatchApplicationError(f"Error parsing diff: {str(e)}", {
            "status": "error",
            "details": {"error": f"Error parsing diff: {str(e)}"}
        })

def apply_diff_with_pipeline_approach(
    file_path: str, diff_content: str, original_content: str
) -> str:
    """
    Apply a diff using the pipeline approach with in-memory content.
    
    This function creates a temporary file with the original content,
    applies the diff using the pipeline approach, and returns the modified content.
    
    Args:
        file_path: Path to the file to modify (used for context only)
        diff_content: The diff content to apply
        original_content: The original file content as a string
        
    Returns:
        The modified file content as a string
    """
    logger.info(f"Applying diff to {file_path} using pipeline approach (in-memory)")
    
    # Validate hunks first
    validate_diff_hunks(diff_content, file_path)
    
    # Create a temporary directory to work in
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a temporary file with the original content
        temp_path = os.path.join(temp_dir, os.path.basename(file_path))
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(original_content)
        
        # Save original environment variables
        original_force_difflib = os.environ.get("ZIYA_FORCE_DIFFLIB")
        original_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        
        # Set up the environment for the pipeline
        os.environ["ZIYA_FORCE_DIFFLIB"] = "1"  # Force using difflib
        os.environ["ZIYA_USER_CODEBASE_DIR"] = temp_dir
        
        try:
            # Apply the diff using the pipeline
            from ..pipeline.pipeline_manager import apply_diff_pipeline
            result = apply_diff_pipeline(diff_content, temp_path)
            
            # Check if the application was successful
            if isinstance(result, dict) and result.get('status') == 'error':
                error_msg = result.get('details', {}).get('error', 'Unknown error')
                raise PatchApplicationError(error_msg, result.get('details', {}))
            
            # Read the modified content
            with open(temp_path, 'r', encoding='utf-8') as f:
                modified_content = f.read()
            
            return modified_content
        finally:
            # Restore environment variables
            if original_force_difflib is not None:
                os.environ["ZIYA_FORCE_DIFFLIB"] = original_force_difflib
            else:
                os.environ.pop("ZIYA_FORCE_DIFFLIB", None)
                
            if original_codebase_dir is not None:
                os.environ["ZIYA_USER_CODEBASE_DIR"] = original_codebase_dir
            else:
                os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
