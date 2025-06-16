"""
Error tracking utilities for diff application.

This module provides classes and functions for tracking detailed error information
throughout the diff application pipeline, ensuring that specific error information
and confidence levels are preserved rather than being overwritten with generic errors.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

@dataclass
class HunkErrorInfo:
    """Class for tracking detailed error information for a hunk."""
    hunk_id: int
    stage: str
    error_type: str
    message: str
    confidence: Optional[float] = None
    position: Optional[int] = None
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "hunk_id": self.hunk_id,
            "stage": self.stage,
            "error_type": self.error_type,
            "message": self.message,
            "confidence": self.confidence,
            "position": self.position,
            "details": self.details
        }

class ErrorTracker:
    """Class for tracking errors throughout the diff application pipeline."""
    
    def __init__(self):
        """Initialize the error tracker."""
        self.hunk_errors: Dict[int, Dict[str, HunkErrorInfo]] = {}
        self.pipeline_errors: List[Dict[str, Any]] = []
    
    def add_hunk_error(self, hunk_id: int, stage: str, error_type: str, message: str,
                      confidence: Optional[float] = None, position: Optional[int] = None,
                      details: Optional[Dict[str, Any]] = None) -> None:
        """
        Add an error for a specific hunk.
        
        Args:
            hunk_id: The ID of the hunk
            stage: The pipeline stage where the error occurred
            error_type: The type of error
            message: The error message
            confidence: The confidence level (for fuzzy matching)
            position: Position information related to the error
            details: Additional error details
        """
        if hunk_id not in self.hunk_errors:
            self.hunk_errors[hunk_id] = {}
        
        self.hunk_errors[hunk_id][stage] = HunkErrorInfo(
            hunk_id=hunk_id,
            stage=stage,
            error_type=error_type,
            message=message,
            confidence=confidence,
            position=position,
            details=details
        )
        
        logger.debug(f"Added error for hunk {hunk_id} in stage {stage}: {message} (type: {error_type}, confidence: {confidence})")
    
    def add_pipeline_error(self, stage: str, error_type: str, message: str,
                         details: Optional[Dict[str, Any]] = None) -> None:
        """
        Add an error for the pipeline as a whole.
        
        Args:
            stage: The pipeline stage where the error occurred
            error_type: The type of error
            message: The error message
            details: Additional error details
        """
        self.pipeline_errors.append({
            "stage": stage,
            "error_type": error_type,
            "message": message,
            "details": details
        })
        
        logger.debug(f"Added pipeline error in stage {stage}: {message} (type: {error_type})")
    
    def get_most_specific_error(self, hunk_id: int) -> Optional[HunkErrorInfo]:
        """
        Get the most specific error for a hunk.
        
        Args:
            hunk_id: The ID of the hunk
            
        Returns:
            The most specific error information, or None if no errors are tracked
        """
        if hunk_id not in self.hunk_errors or not self.hunk_errors[hunk_id]:
            return None
        
        # Define the stage priority order (later stages are more specific)
        stage_priority = {
            "initialization": 0,
            "system_patch": 1,
            "git_apply": 2,
            "difflib": 3,
            "llm_resolver": 4,
            "complete": 5
        }
        
        # Sort errors by stage priority (higher priority first)
        sorted_errors = sorted(
            self.hunk_errors[hunk_id].values(),
            key=lambda e: stage_priority.get(e.stage, -1),
            reverse=True
        )
        
        # Further prioritize errors with confidence information
        errors_with_confidence = [e for e in sorted_errors if e.confidence is not None]
        if errors_with_confidence:
            # Return the error with the highest confidence
            return max(errors_with_confidence, key=lambda e: e.confidence or 0)
        
        # If no errors have confidence information, return the most recent error
        return sorted_errors[0] if sorted_errors else None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the error tracker to a dictionary for API response."""
        most_specific_errors = {}
        for hunk_id in self.hunk_errors:
            most_specific = self.get_most_specific_error(hunk_id)
            if most_specific:
                most_specific_errors[str(hunk_id)] = most_specific.to_dict()
        
        return {
            "most_specific_errors": most_specific_errors,
            "pipeline_errors": self.pipeline_errors
        }

def extract_error_info(error_obj: Any) -> Dict[str, Any]:
    """
    Extract error information from an exception or error object.
    
    Args:
        error_obj: The error object to extract information from
        
    Returns:
        Dictionary with error information
    """
    error_info = {
        "message": str(error_obj),
        "type": "unknown"
    }
    
    # Check if it's a PatchApplicationError with details
    if hasattr(error_obj, 'details'):
        error_info["details"] = getattr(error_obj, 'details', {})
        error_info["type"] = error_info["details"].get('type', 'application_error')
    
    # Check if it's a standard exception
    if isinstance(error_obj, Exception):
        error_info["type"] = error_obj.__class__.__name__
    
    return error_info
