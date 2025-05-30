"""
Pipeline module for managing the flow of diff application.

This module provides a structured pipeline for applying diffs, tracking each hunk
through the various stages of application (system patch, git apply, difflib, LLM resolver).
"""

from .diff_pipeline import DiffPipeline, PipelineStage, HunkStatus, PipelineResult
from .pipeline_manager import apply_diff_pipeline
