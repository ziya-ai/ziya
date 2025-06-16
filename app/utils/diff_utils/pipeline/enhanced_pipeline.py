"""
Enhanced pipeline classes for managing diff application with improved error tracking.

This module extends the core pipeline classes with enhanced error tracking
to preserve detailed error information throughout the pipeline.
"""

from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
import logging
from enum import Enum

from .diff_pipeline import DiffPipeline, PipelineStage, HunkStatus, HunkTracker, PipelineResult
from ..core.error_tracking import PipelineErrorTracker, StageError

logger = logging.getLogger(__name__)

class EnhancedHunkTracker(HunkTracker):
    """Enhanced version of HunkTracker with improved error tracking."""
    
    def __init__(self, hunk_id: int, hunk_data: Dict[str, Any]):
        """Initialize the enhanced hunk tracker."""
        super().__init__(hunk_id=hunk_id, hunk_data=hunk_data)
        self.stage_errors: Dict[PipelineStage, Dict[str, Any]] = {}
    
    def update_status(self, stage: PipelineStage, status: HunkStatus, 
                     confidence: float = 0.0, position: Optional[int] = None,
                     error_details: Optional[Dict[str, Any]] = None) -> None:
        """
        Update the status of this hunk for a given pipeline stage with enhanced error tracking.
        
        Args:
            stage: The pipeline stage
            status: The new status
            confidence: Confidence score (for fuzzy matching)
            position: Position where the hunk was applied
            error_details: Details of any error that occurred
        """
        # Call the parent method to update basic status
        super().update_status(stage, status, confidence, position, error_details)
        
        # Store detailed error information for this stage
        if status == HunkStatus.FAILED and error_details:
            self.stage_errors[stage] = {
                "error_details": error_details,
                "confidence": confidence,
                "position": position
            }
            logger.debug(f"Stored detailed error for hunk {self.hunk_id} in stage {stage.value}: {error_details}")
    
    def get_most_specific_error(self) -> Optional[Dict[str, Any]]:
        """
        Get the most specific error information available for this hunk.
        
        Returns:
            The most specific error information, or None if no errors are tracked
        """
        if not self.stage_errors:
            return None
        
        # Define the stage priority order (later stages are more specific)
        stage_priority = {
            PipelineStage.INIT: 0,
            PipelineStage.SYSTEM_PATCH: 1,
            PipelineStage.GIT_APPLY: 2,
            PipelineStage.DIFFLIB: 3,
            PipelineStage.LLM_RESOLVER: 4,
            PipelineStage.COMPLETE: 5
        }
        
        # Sort errors by stage priority (higher priority first)
        sorted_stages = sorted(
            self.stage_errors.keys(),
            key=lambda s: stage_priority.get(s, -1),
            reverse=True
        )
        
        # Further prioritize errors with confidence information
        stages_with_confidence = [s for s in sorted_stages 
                                if self.stage_errors[s].get('confidence') is not None]
        
        if stages_with_confidence:
            # Return the error with the highest confidence
            best_stage = max(stages_with_confidence, 
                           key=lambda s: self.stage_errors[s].get('confidence') or 0)
            return {
                "stage": best_stage.value,
                "error_details": self.stage_errors[best_stage].get('error_details'),
                "confidence": self.stage_errors[best_stage].get('confidence'),
                "position": self.stage_errors[best_stage].get('position')
            }
        
        # If no errors have confidence information, return the most recent error
        if sorted_stages:
            best_stage = sorted_stages[0]
            return {
                "stage": best_stage.value,
                "error_details": self.stage_errors[best_stage].get('error_details'),
                "confidence": self.stage_errors[best_stage].get('confidence'),
                "position": self.stage_errors[best_stage].get('position')
            }
        
        return None

class EnhancedPipelineResult(PipelineResult):
    """Enhanced version of PipelineResult with improved error tracking."""
    
    def __init__(self, file_path: str, original_diff: str):
        """Initialize the enhanced pipeline result."""
        super().__init__(file_path=file_path, original_diff=original_diff)
        self.error_tracker = PipelineErrorTracker()
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the pipeline result to a dictionary with enhanced error information.
        
        Returns:
            Dictionary with detailed result information including enhanced error tracking
        """
        # Get the basic result dictionary from the parent class
        result_dict = super().to_dict()
        
        # Add enhanced error information
        hunk_errors = {}
        for hunk_id, tracker in self.hunks.items():
            if isinstance(tracker, EnhancedHunkTracker):
                specific_error = tracker.get_most_specific_error()
                if specific_error:
                    hunk_errors[str(hunk_id)] = specific_error
        
        # Update the hunk_statuses with enhanced error information
        for hunk_id, error_info in hunk_errors.items():
            if hunk_id in result_dict["hunk_statuses"]:
                result_dict["hunk_statuses"][hunk_id]["enhanced_error"] = error_info
        
        # Add a new section for enhanced error tracking
        result_dict["enhanced_errors"] = {
            "hunk_errors": hunk_errors
        }
        
        return result_dict

class EnhancedDiffPipeline(DiffPipeline):
    """Enhanced version of DiffPipeline with improved error tracking."""
    
    def __init__(self, file_path: str, diff_content: str):
        """Initialize the enhanced diff pipeline."""
        # Initialize with basic attributes
        self.file_path = file_path
        self.original_diff = diff_content
        self.current_diff = diff_content
        
        # Create an enhanced pipeline result
        self.result = EnhancedPipelineResult(file_path=file_path, original_diff=diff_content)
        self.current_stage = PipelineStage.INIT
    
    def initialize_hunks(self, hunks: List[Dict[str, Any]]) -> None:
        """
        Initialize the hunks to track through the pipeline with enhanced tracking.
        
        Args:
            hunks: List of hunk dictionaries from parse_unified_diff_exact_plus
        """
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk.get('number', i)
            self.result.hunks[hunk_id] = EnhancedHunkTracker(
                hunk_id=hunk_id,
                hunk_data=hunk
            )
    
    def update_hunk_status(self, hunk_id: int, stage: PipelineStage, status: HunkStatus,
                          confidence: float = 0.0, position: Optional[int] = None,
                          error_details: Optional[Dict[str, Any]] = None) -> None:
        """
        Update the status of a hunk with enhanced error tracking.
        
        Args:
            hunk_id: ID of the hunk to update
            stage: The pipeline stage
            status: The new status
            confidence: Confidence score (for fuzzy matching)
            position: Position where the hunk was applied
            error_details: Details of any error that occurred
        """
        # Call the parent method to update basic status
        super().update_hunk_status(hunk_id, stage, status, confidence, position, error_details)
        
        # Add error information to the error tracker if this is a failure
        if status == HunkStatus.FAILED and error_details:
            error_message = error_details.get('error', 'Unknown error')
            error_type = error_details.get('type', 'application_error')
            
            # Add to the error tracker
            if hasattr(self.result, 'error_tracker'):
                self.result.error_tracker.add_hunk_error(
                    hunk_id=hunk_id,
                    stage=stage.value,
                    message=error_message,
                    error_type=error_type,
                    confidence=confidence,
                    details=error_details,
                    position=position
                )
    
    def complete(self, error: Optional[str] = None) -> EnhancedPipelineResult:
        """
        Complete the pipeline and return the enhanced result.
        
        Args:
            error: Optional error message
            
        Returns:
            The enhanced pipeline result
        """
        # Call the parent method to complete the pipeline
        super().complete(error)
        
        # Add pipeline-level error to the error tracker if present
        if error and hasattr(self.result, 'error_tracker'):
            self.result.error_tracker.add_pipeline_error(
                stage=self.current_stage.value,
                message=error,
                error_type="pipeline_error"
            )
        
        return self.result
