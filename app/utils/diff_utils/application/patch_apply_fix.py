"""
Fixed patch application utilities with improved error reporting.

This module provides fixes for the patch_apply.py module to preserve detailed error information
throughout the diff application process, particularly fixing the issue where specific error
information and confidence levels are overwritten with generic 0.0 confidence errors.
"""

from typing import List, Optional, Dict, Any
import logging

from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import parse_unified_diff_exact_plus
from ..validation.validators import is_hunk_already_applied

# Configure logging
logger = logging.getLogger(__name__)

def apply_diff_with_difflib_fixed(file_path: str, diff_content: str, skip_hunks: List[int] = None) -> str:
    """
    Apply a diff using difflib with improved error reporting.
    This is a fixed version of apply_diff_with_difflib that preserves detailed error information.
    
    Args:
        file_path: Path to the file to modify
        diff_content: The diff content to apply
        skip_hunks: Optional list of hunk IDs to skip (already applied)
        
    Returns:
        The modified file content as a string
    """
    logger.info(f"Applying diff to {file_path} using difflib with improved error reporting")
    
    # Initialize skip_hunks if not provided
    if skip_hunks is None:
        skip_hunks = []
    
    if skip_hunks:
        logger.info(f"Skipping already applied hunks: {skip_hunks}")
    
    # Read the file content
    with open(file_path, 'r', encoding='utf-8') as f:
        original_content = f.read()
        original_lines = original_content.splitlines(True)  # Keep line endings
    
    # Parse hunks
    hunks = list(parse_unified_diff_exact_plus(diff_content, file_path))
    if not hunks:
        logger.warning("No hunks parsed from diff content.")
        return original_content
    
    logger.debug(f"Parsed {len(hunks)} hunks for difflib")
    
    # Check if all hunks are already applied
    all_already_applied = True
    for hunk in hunks:
        if hunk.get('number') not in skip_hunks:
            # Check if this hunk is already applied anywhere in the file
            hunk_applied = False
            for pos in range(len(original_lines) + 1):
                if is_hunk_already_applied(original_lines, hunk, pos):
                    hunk_applied = True
                    logger.info(f"Hunk #{hunk.get('number')} is already applied at position {pos}")
                    break
            
            if not hunk_applied:
                all_already_applied = False
                break
    
    if all_already_applied:
        logger.info("All hunks already applied, returning original content")
        raise PatchApplicationError("All hunks already applied", {"type": "already_applied"})
    
    # Track detailed error information for each hunk
    hunk_errors = {}
    
    # Try to apply the diff using the hybrid forced mode
    from .patch_apply import apply_diff_with_difflib_hybrid_forced
    
    try:
        return ''.join(apply_diff_with_difflib_hybrid_forced(file_path, diff_content, original_lines, skip_hunks))
    except PatchApplicationError as e:
        # Extract and preserve detailed error information
        if hasattr(e, 'details'):
            failures = e.details.get('failures', [])
            
            # Track each failure with detailed information
            for failure in failures:
                details = failure.get('details', {})
                hunk_id = details.get('hunk')
                if hunk_id:
                    hunk_errors[hunk_id] = {
                        "message": failure.get('message', 'Unknown error'),
                        "type": details.get('type', 'unknown'),
                        "confidence": details.get('confidence', 0.0),
                        "position": details.get('position'),
                        "details": details
                    }
                    # Log the extracted error information
                    logger.debug(f"Extracted error for hunk {hunk_id}: confidence={details.get('confidence')}")
        
        # Create a new exception with preserved error information
        error_details = {
            "type": "application_failed",
            "hunk_errors": hunk_errors,
            "original_error": e.details if hasattr(e, 'details') else {}
        }
        
        # Log the error details for debugging
        logger.debug(f"Re-raising PatchApplicationError with preserved error details: {error_details}")
        
        # Raise the new exception
        raise PatchApplicationError(
            f"Failed to apply diff: {str(e)}",
            error_details
        )
    except Exception as e:
        # Handle unexpected exceptions
        # Truncate partial_content to avoid massive log dumps
        error_str = str(e)
        if "'partial_content':" in error_str and len(error_str) > 500:
            parts = error_str.split("'partial_content':")
            summary = parts[0].rstrip(", {")[:300]
            logger.error(f"Error applying diff: {summary}...[content truncated]")
        else:
            logger.error(f"Error applying diff: {error_str[:500]}")
        
        raise PatchApplicationError(
            f"Failed to apply diff: {str(e)}",
            {
                "type": "application_failed",
                "error": str(e),
                "exception_type": e.__class__.__name__
            }
        )
