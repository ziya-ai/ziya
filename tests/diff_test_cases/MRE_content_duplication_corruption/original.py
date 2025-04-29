"""
Pipeline manager for applying diffs.

This module provides the main entry point for the diff application pipeline,
coordinating the flow through system patch, git apply, difflib, and LLM resolver.
"""

import os
import json
import subprocess
import tempfile
from typing import Dict, List, Any, Optional, Tuple

from app.utils.logging_utils import logger
from ..core.exceptions import PatchApplicationError
from ..parsing.diff_parser import parse_unified_diff_exact_plus, extract_target_file_from_diff, split_combined_diff
from ..validation.validators import is_new_file_creation, is_hunk_already_applied, normalize_line_for_comparison
from ..file_ops.file_handlers import create_new_file, cleanup_patch_artifacts
from ..application.patch_apply import apply_diff_with_difflib, apply_diff_with_difflib_hybrid_forced
from ..application.git_diff import parse_patch_output

from .diff_pipeline import DiffPipeline, PipelineStage, HunkStatus, PipelineResult


def apply_diff_pipeline(git_diff: str, file_path: str) -> Dict[str, Any]:
    """
    Apply a git diff using a structured pipeline approach.
    
    This function manages the flow through the various stages of diff application:
    1. System patch (using the patch command)
    2. Git apply (using git apply)
    3. Difflib (using Python's difflib)
    4. LLM resolver (for complex cases)
    
    Each hunk is tracked through the pipeline, with its status updated at each stage.
    
    Args:
        git_diff: The git diff to apply
        file_path: Path to the target file
        
    Returns:
        A dictionary with the result of the pipeline
    """
    pipeline = DiffPipeline(file_path, git_diff)
    pipeline.update_stage(PipelineStage.INIT)
    
    # Split combined diffs if present
    individual_diffs = split_combined_diff(git_diff)
    if len(individual_diffs) > 1:
