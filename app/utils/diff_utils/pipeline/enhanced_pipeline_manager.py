"""
Enhanced pipeline manager for applying diffs with improved error tracking and fuzzy matching.

This module extends the core pipeline manager with enhanced error tracking
and improved fuzzy matching to increase the success rate of diff application.
"""

import os
import logging
from typing import Dict, List, Any, Optional

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..core.error_tracking import ErrorTracker
from .pipeline_manager import apply_diff_pipeline
from .diff_pipeline import PipelineStage, HunkStatus
from ..application.enhanced_patch_apply import apply_diff_with_enhanced_matching_wrapper

def apply_diff_pipeline_with_enhancements(
    git_diff: str, 
    file_path: str, 
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Apply a git diff using a structured pipeline approach with enhanced error tracking
    and improved fuzzy matching.
    
    This function wraps the standard pipeline manager and enhances it with:
    1. Improved error tracking to preserve detailed error information
    2. Enhanced fuzzy matching to increase the success rate of diff application
    
    Args:
        git_diff: The git diff to apply
        file_path: Path to the target file
        request_id: (Optional) request ID for tracking
        
    Returns:
        A dictionary with the result of the pipeline, including enhanced error information
    """
    logger.info("Starting diff application pipeline with enhancements...")
    
    # Initialize error tracker
    error_tracker = ErrorTracker()
    
    # Check if ZIYA_USE_ENHANCED_MATCHING is set
    use_enhanced_matching = os.environ.get('ZIYA_USE_ENHANCED_MATCHING', '0') == '1'
    
    try:
        if use_enhanced_matching:
            logger.info("Using enhanced matching for diff application")
            
            # Try to apply the diff with enhanced matching
            try:
                # Apply the diff with enhanced matching
                modified_content = apply_diff_with_enhanced_matching_wrapper(file_path, git_diff)
                
                # If successful, return a success result
                return {
                    "status": "success",
                    "message": "Changes applied successfully with enhanced matching.",
                    "succeeded": [1],  # Simplified for now
                    "failed": [],
                    "already_applied": [],
                    "changes_written": True,
                    "enhanced_errors": error_tracker.to_dict()
                }
            except PatchApplicationError as e:
                # Extract error information
                if hasattr(e, 'details'):
                    failures = e.details.get('failures', [])
                    
                    # Track each failure
                    for failure in failures:
                        details = failure.get('details', {})
                        hunk_id = details.get('hunk')
                        if hunk_id:
                            error_tracker.add_hunk_error(
                                hunk_id=hunk_id,
                                stage="enhanced_matching",
                                error_type=details.get('type', 'unknown'),
                                message=failure.get('message', 'Unknown error'),
                                confidence=details.get('confidence'),
                                position=details.get('position'),
                                details=details
                            )
                
                # Fall back to standard pipeline
                logger.info("Enhanced matching failed, falling back to standard pipeline")
            except Exception as e:
                # For other exceptions, add a generic error
                error_tracker.add_pipeline_error(
                    stage="enhanced_matching",
                    error_type="unexpected_error",
                    message=str(e),
                    details={"exception_type": e.__class__.__name__}
                )
                
                # Fall back to standard pipeline
                logger.info(f"Enhanced matching failed: {str(e)}, falling back to standard pipeline")
        
        # Call the standard pipeline manager
        result = apply_diff_pipeline(git_diff, file_path, request_id)
        
        # Extract error information from the result
        if result.get('status') in ('error', 'partial'):
            # Extract hunk-specific errors
            hunk_statuses = result.get('hunk_statuses', {})
            for hunk_id_str, status in hunk_statuses.items():
                try:
                    hunk_id = int(hunk_id_str)
                except ValueError:
                    continue
                
                if status.get('status') == 'failed':
                    error_details = status.get('error_details', {})
                    error_type = error_details.get('error', 'unknown')
                    message = error_details.get('details', f"Failed to apply hunk {hunk_id}")
                    
                    error_tracker.add_hunk_error(
                        hunk_id=hunk_id,
                        stage=status.get('stage', 'unknown'),
                        error_type=error_type,
                        message=message,
                        confidence=status.get('confidence'),
                        position=status.get('position'),
                        details=error_details
                    )
            
            # Extract pipeline-level error
            if result.get('error'):
                error_tracker.add_pipeline_error(
                    stage=result.get('current_stage', 'unknown'),
                    error_type="pipeline_error",
                    message=result.get('error'),
                    details=result.get('details', {})
                )
        
        # Add enhanced error information to the result
        result['enhanced_errors'] = error_tracker.to_dict()
        
        return result
    except Exception as e:
        # Handle unexpected exceptions
        logger.error(f"Unexpected error in enhanced pipeline: {str(e)}")
        
        error_tracker.add_pipeline_error(
            stage="pipeline",
            error_type="unexpected_error",
            message=str(e),
            details={"exception_type": e.__class__.__name__}
        )
        
        # Return error result with enhanced error information
        return {
            "status": "error",
            "message": f"Unexpected error: {str(e)}",
            "error": str(e),
            "enhanced_errors": error_tracker.to_dict(),
            "succeeded": [],
            "failed": [],
            "already_applied": [],
            "changes_written": False
        }
