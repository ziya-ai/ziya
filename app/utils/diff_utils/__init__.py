"""
diff_utils package - Utilities for handling diffs and patches.

This package provides functionality for parsing, validating, and applying diffs and patches.
It is designed to be modular and maintainable, with clear separation of concerns.
"""

# Core utilities
from .core import PatchApplicationError, clamp, normalize_escapes, calculate_block_similarity

# Parsing utilities
from .parsing import parse_unified_diff, parse_unified_diff_exact_plus
from .parsing import extract_target_file_from_diff, split_combined_diff

# Validation utilities
from .validation import is_new_file_creation, is_hunk_already_applied

# Application utilities
from .application import apply_diff_with_difflib, apply_diff_with_difflib_hybrid_forced
from .application import use_git_to_apply_code_diff

# File operation utilities
from .file_ops import create_new_file, cleanup_patch_artifacts

# Pipeline utilities
from .pipeline import apply_diff_pipeline, DiffPipeline, PipelineStage, HunkStatus, PipelineResult
