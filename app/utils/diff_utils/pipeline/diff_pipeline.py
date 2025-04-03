"""
Core pipeline classes for managing diff application.

This module defines the data structures and classes used to track hunks
through the various stages of the diff application pipeline.
"""

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
            
        if error_details:
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
        return (self.is_complete and 
                all(tracker.status in (HunkStatus.SUCCEEDED, HunkStatus.ALREADY_APPLIED) 
                    for tracker in self.hunks.values()))
    
    @property
    def is_partial_success(self) -> bool:
        """Check if the pipeline partially succeeded (some hunks applied)."""
        return (self.changes_written and 
                any(tracker.status == HunkStatus.SUCCEEDED for tracker in self.hunks.values()) and
                any(tracker.status == HunkStatus.FAILED for tracker in self.hunks.values()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the pipeline result to a dictionary."""
        # Create a detailed dictionary with hunk-by-hunk status information
        hunk_details = {}
        for hunk_id, tracker in self.hunks.items():
            hunk_details[str(hunk_id)] = {
                "status": tracker.status.value,
                "stage": tracker.current_stage.value,
                "confidence": tracker.confidence,
                "position": tracker.position,
                "error_details": tracker.error_details
            }
            
        return {
            "status": "success" if self.is_success else 
                     "partial" if self.is_partial_success else 
                     "error",
            "details": {
                "succeeded": self.succeeded_hunks,
                "failed": self.failed_hunks,
                "already_applied": self.already_applied_hunks,
                "changes_written": self.changes_written,
                "error": self.error,
                "hunk_statuses": hunk_details  # Add detailed hunk status information
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
        
        # Extract the remaining hunks
        return extract_remaining_hunks(self.original_diff, hunk_status)
    
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
