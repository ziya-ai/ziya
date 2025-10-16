"""
Core pipeline classes for managing diff application.

This module defines the data structures and classes used to track hunks
through the various stages of the diff application pipeline.
"""
from app.utils.logging_utils import logger # Import logger

import enum
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field

class PipelineStage(enum.Enum):
    """Enum representing the stages of the diff application pipeline."""
    INIT = "initialization"
    SYSTEM_PATCH = "system_patch"
    GIT_APPLY = "git_apply"
    DIFFLIB = "difflib"
    LLM_RESOLVER = "llm_resolver"
    COMPLETE = "complete"

class HunkStatus(enum.Enum):
    """Enum representing the status of a hunk in the pipeline."""
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ALREADY_APPLIED = "already_applied"
    SKIPPED = "skipped"

@dataclass
class HunkTracker:
    """Class for tracking a hunk through the pipeline."""
    hunk_id: int
    hunk_data: Dict[str, Any]
    status: HunkStatus = HunkStatus.PENDING
    current_stage: PipelineStage = PipelineStage.INIT
    stage_results: Dict[PipelineStage, HunkStatus] = field(default_factory=dict)
    confidence: float = 0.0
    position: Optional[int] = None
    error_details: Optional[Dict[str, Any]] = None
    
    def update_status(self, stage: PipelineStage, status: HunkStatus, 
                     confidence: float = 0.0, position: Optional[int] = None,
                     error_details: Optional[Dict[str, Any]] = None) -> None:
        """Update the status of this hunk for a given pipeline stage."""
        self.stage_results[stage] = status
        self.status = status
        self.current_stage = stage
        
        if confidence > 0:
            self.confidence = confidence
        
        if position is not None:
            self.position = position
            
        # Clear error details if the hunk succeeded or was already applied
        if status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED):
            self.error_details = None
        else:
            # Otherwise, update with the provided details (which might be None)
            self.error_details = error_details

    def is_complete(self) -> bool:
        """Check if this hunk has completed the pipeline."""
        return (self.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) or 
                self.current_stage == PipelineStage.COMPLETE)

@dataclass
class PipelineResult:
    """Class representing the result of the diff application pipeline."""
    file_path: str
    original_diff: str
    hunks: Dict[int, HunkTracker] = field(default_factory=dict)
    stages_completed: List[PipelineStage] = field(default_factory=list)
    current_stage: PipelineStage = PipelineStage.INIT
    changes_written: bool = False
    error: Optional[str] = None
    status: str = "pending"  # Add a status field to track the overall status
    request_id: Optional[str] = None  # Store the request ID for tracking
    _parsed_hunks_cache: Optional[List[Dict[str, Any]]] = field(default=None, init=False, repr=False)  # Cache for parsed hunks

    # Removed redundant methods and properties to avoid confusion
    # We'll use the existing succeeded_hunks, failed_hunks, etc. properties consistently

    def determine_final_status(self) -> str:
        """Determines the overall status based on hunk outcomes."""
        # Check for an explicit pipeline-level error first
        if self.error and not self.changes_written:
            logger.debug(f"Determining final status: Explicit error found: {self.error}")
            return "error"

        succeeded_count = len(self.succeeded_hunks)
        failed_count = len(self.failed_hunks)
        already_applied_count = len(self.already_applied_hunks)
        total_hunks = succeeded_count + failed_count + already_applied_count

        logger.debug(f"Determining final status: S={succeeded_count}, F={failed_count}, A={already_applied_count}, ChangesWritten={self.changes_written}")

        # If we have any failed hunks AND any succeeded/already applied hunks, it's partial
        if failed_count > 0 and (succeeded_count > 0 or already_applied_count > 0):
            return "partial"
        # Special case: if changes were written but no hunks are tracked (like file creation), it's success
        elif self.changes_written and total_hunks == 0:
            logger.debug("Changes written with no tracked hunks - treating as success (likely file creation)")
            return "success"
        # If all hunks failed and no changes were written, it's an error
        elif failed_count > 0 and succeeded_count == 0 and already_applied_count == 0:
            return "error"
        # If all hunks succeeded or were already applied, it's success
        elif failed_count == 0 and (succeeded_count > 0 or already_applied_count > 0):
            return "success"
        # Default case (e.g., empty diff or new file creation where changes_written is true)
        else:
            # If changes were written (like new file creation), it's success. Otherwise, it's success (no-op).
            return "success" if self.changes_written else "success" # Treat no-op/empty diff/new file as success if no errors occurred
    def get_summary_message(self) -> str:
        """Generates a user-friendly summary message based on the final status."""
        final_status = self.determine_final_status()
        if final_status == "success":
             if self.changes_written:
                 return "Changes applied successfully."
             elif len(self.already_applied_hunks) > 0:
                 return "No changes needed; hunks were already applied."
             else:
                 return "No changes needed or diff was empty."
        elif final_status == "partial":
            return f"Some changes applied, but {len(self.failed_hunks)} hunk(s) failed."
        elif final_status == "error":
             if self.error:
                 return f"Error applying changes: {self.error}"
             elif len(self.failed_hunks) > 0:
                 return f"Failed to apply changes: {len(self.failed_hunks)} hunk(s) failed."
             else:
                 return "Failed to apply changes: Unknown error."
        return "Diff application status unknown."
    
    @property
    def succeeded_hunks(self) -> List[int]:
        """Get the IDs of hunks that succeeded."""
        return [hunk_id for hunk_id, tracker in self.hunks.items() 
                if tracker.status == HunkStatus.SUCCEEDED]
    
    @property
    def failed_hunks(self) -> List[int]:
        """Get the IDs of hunks that failed."""
        return [hunk_id for hunk_id, tracker in self.hunks.items() 
                if tracker.status == HunkStatus.FAILED]
    
    @property
    def already_applied_hunks(self) -> List[int]:
        """Get the IDs of hunks that were already applied."""
        return [hunk_id for hunk_id, tracker in self.hunks.items() 
                if tracker.status == HunkStatus.ALREADY_APPLIED]
    
    @property
    def pending_hunks(self) -> List[int]:
        """Get the IDs of hunks that are still pending."""
        return [hunk_id for hunk_id, tracker in self.hunks.items() 
                if tracker.status == HunkStatus.PENDING]
    
    @property
    def is_complete(self) -> bool:
        """Check if the pipeline is complete."""
        return (self.current_stage == PipelineStage.COMPLETE or
                all(tracker.is_complete() for tracker in self.hunks.values()))
    
    @property
    def is_success(self) -> bool:
        """Check if the pipeline succeeded (all hunks applied or already applied)."""

        # Use the centralized logic
        return self.determine_final_status() == "success"

    @property
    def is_partial_success(self) -> bool:
        """Check if the pipeline partially succeeded (some hunks applied)."""
        # Use the centralized logic
        return self.determine_final_status() == "partial"
    def to_dict(self) -> Dict[str, Any]:
        """Convert the pipeline result to a dictionary for API response details."""
        # Clean up error details for already applied hunks
        for hunk_id in self.already_applied_hunks:
            if hunk_id in self.hunks and self.hunks[hunk_id].error_details:
                self.hunks[hunk_id].error_details = None
        
        # Create a detailed dictionary with hunk-by-hunk status information
        already_applied_hunks = self.already_applied_hunks.copy()
        hunk_details = {}
        for hunk_id, tracker in self.hunks.items():
            hunk_details[str(hunk_id)] = {
                "status": tracker.status.value,
                "stage": tracker.current_stage.value,
                "confidence": tracker.confidence,
                "position": tracker.position,
                "error_details": tracker.error_details
            }
                
            # add it to the already_applied_hunks list
            if tracker.status == HunkStatus.ALREADY_APPLIED and hunk_id not in already_applied_hunks:
                already_applied_hunks.append(hunk_id)
        
        # Debug logging to understand what's happening
        from app.utils.logging_utils import logger
        logger.info("=== DEBUG: PipelineResult.to_dict() ===")
        logger.info(f"Current status: {self.status}")
        logger.info(f"Succeeded hunks: {self.succeeded_hunks}")
        logger.info(f"Failed hunks: {self.failed_hunks}")
        logger.info(f"Already applied hunks: {already_applied_hunks}")
        logger.info(f"Changes written: {self.changes_written}")
        logger.info(f"is_success property: {self.is_success}")
        logger.info(f"is_partial_success property: {self.is_partial_success}")
        logger.info(f"Error: {self.error}")
        
        # Determine the final status based on hunk outcomes
        final_status = self.determine_final_status()
        
        # Generate a user-friendly message
        final_message = self.get_summary_message()
        
        # Use the actual lists, not property methods
        return {
            "status": final_status,
            "request_id": self.request_id,
            "message": final_message,
            "succeeded": self.succeeded_hunks,
            "failed": self.failed_hunks,
            "already_applied": already_applied_hunks, # Use the locally corrected list
            "changes_written": self.changes_written,
            "request_id": self.request_id,  # Include the request ID in the response
            "error": self.error,
            "hunk_statuses": hunk_details,
            "details": {
                "succeeded": self.succeeded_hunks,
                "failed": self.failed_hunks,
                "already_applied": already_applied_hunks,
                "hunk_statuses": hunk_details
            }
        }

class DiffPipeline:
    """
    Class for managing the diff application pipeline.
    
    This class tracks hunks through the various stages of the pipeline,
    from system patch to git apply to difflib to LLM resolver.
    """
    
    def __init__(self, file_path: str, diff_content: str):
        """
        Initialize the pipeline.
        
        Args:
            file_path: Path to the file to modify
            diff_content: The diff content to apply
        """
        self.file_path = file_path
        self.original_diff = diff_content
        self.current_diff = diff_content
        self.result = PipelineResult(file_path=file_path, original_diff=diff_content)
        self.current_stage = PipelineStage.INIT
        self._file_content_cache: Optional[Tuple[str, List[str]]] = None  # Cache for file content
    
    def get_parsed_hunks(self) -> List[Dict[str, Any]]:
        """
        Get parsed hunks with caching to avoid redundant parsing.
        
        Returns:
            List of parsed hunk dictionaries
        """
        if self.result._parsed_hunks_cache is None:
            from ..parsing.diff_parser import parse_unified_diff_exact_plus
            self.result._parsed_hunks_cache = list(parse_unified_diff_exact_plus(
                self.current_diff, self.file_path))
        return self.result._parsed_hunks_cache
    
    def get_file_content(self) -> Tuple[str, List[str]]:
        """
        Get file content with caching to avoid redundant reads.
        
        Returns:
            Tuple of (full_content, lines_list)
        """
        if self._file_content_cache is None:
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.splitlines()
                self._file_content_cache = (content, lines)
            except FileNotFoundError:
                self._file_content_cache = ("", [])
        return self._file_content_cache
    
    def invalidate_file_cache(self) -> None:
        """Invalidate the file content cache after modifications."""
        self._file_content_cache = None
        
    def initialize_hunks(self, hunks: List[Dict[str, Any]]) -> None:
        """
        Initialize the hunks to track through the pipeline.
        
        Args:
            hunks: List of hunk dictionaries from parse_unified_diff_exact_plus
        """
        for i, hunk in enumerate(hunks, 1):
            hunk_id = hunk.get('number', i)
            self.result.hunks[hunk_id] = HunkTracker(
                hunk_id=hunk_id,
                hunk_data=hunk,
                status=HunkStatus.PENDING,
                current_stage=PipelineStage.INIT
            )
    
    def update_hunk_status(self, hunk_id: int, stage: PipelineStage, status: HunkStatus,
                          confidence: float = 0.0, position: Optional[int] = None,
                          error_details: Optional[Dict[str, Any]] = None) -> None:
        """
        Update the status of a hunk for a given pipeline stage.
        
        Args:
            hunk_id: ID of the hunk to update
            stage: The pipeline stage
            status: The new status
            confidence: Confidence score (for fuzzy matching)
            position: Position where the hunk was applied
            error_details: Details of any error that occurred
        """
        if hunk_id in self.result.hunks:
            self.result.hunks[hunk_id].update_status(
                stage=stage,
                status=status,
                confidence=confidence,
                position=position,
                error_details=error_details
            )
            
            # Update the appropriate lists based on the status
            if status == HunkStatus.SUCCEEDED:
                if hunk_id not in self.result.succeeded_hunks:
                    self.result.succeeded_hunks.append(hunk_id)
                # Remove from other lists if present
                if hunk_id in self.result.failed_hunks:
                    self.result.failed_hunks.remove(hunk_id)
                if hunk_id in self.result.already_applied_hunks:
                    self.result.already_applied_hunks.remove(hunk_id)
            elif status == HunkStatus.FAILED:
                if hunk_id not in self.result.failed_hunks:
                    self.result.failed_hunks.append(hunk_id)
                # Remove from other lists if present
                if hunk_id in self.result.succeeded_hunks:
                    self.result.succeeded_hunks.remove(hunk_id)
                if hunk_id in self.result.already_applied_hunks:
                    self.result.already_applied_hunks.remove(hunk_id)
            elif status == HunkStatus.ALREADY_APPLIED:
                if hunk_id not in self.result.already_applied_hunks:
                    self.result.already_applied_hunks.append(hunk_id)
                # Remove from other lists if present
                if hunk_id in self.result.succeeded_hunks:
                    self.result.succeeded_hunks.remove(hunk_id)
                if hunk_id in self.result.failed_hunks:
                    self.result.failed_hunks.remove(hunk_id)
    
    def update_stage(self, stage: PipelineStage) -> None:
        """
        Update the current stage of the pipeline.
        
        Args:
            stage: The new pipeline stage
        """
        self.current_stage = stage
        self.result.current_stage = stage
        self.result.stages_completed.append(stage)
    
    def extract_remaining_hunks(self) -> str:
        """
        Extract hunks that still need to be processed.
        
        Returns:
            A diff containing only the hunks that need further processing
        """
        from ..application.git_diff import extract_remaining_hunks
        
        # Create a dictionary mapping hunk IDs to their status (True if succeeded/already applied, False otherwise)
        hunk_status = {
            hunk_id: tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED)
            for hunk_id, tracker in self.result.hunks.items()
        }
        
        # For multiple file changes, we need to ensure we preserve the original structure
        # If all hunks are pending/failed, just return the original diff
        if not any(hunk_status.values()):
            return self.original_diff
            
        # Extract the remaining hunks
        return extract_remaining_hunks(self.original_diff, hunk_status)
    
    def reset_failed_hunks_to_pending(self) -> int:
        """
        Reset all failed hunks to pending status so they can be processed by the next stage.
        
        Returns:
            Number of hunks that were reset
        """
        reset_count = 0
        for hunk_id, tracker in self.result.hunks.items():
            if tracker.status == HunkStatus.FAILED:
                self.update_hunk_status(
                    hunk_id=hunk_id,
                    stage=self.current_stage,
                    status=HunkStatus.PENDING,
                    error_details=None  # Clear previous errors
                )
                reset_count += 1
        
        return reset_count
    
    def complete(self, error: Optional[str] = None) -> PipelineResult:
        """
        Complete the pipeline and return the result.
        
        Args:
            error: Optional error message
            
        Returns:
            The pipeline result
        """
        self.update_stage(PipelineStage.COMPLETE)
        self.result.error = error
        return self.result
