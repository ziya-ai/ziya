"""
Application utilities for the diff_utils package.

This module provides functionality for applying diffs and patches to files.
"""

from .patch_apply import apply_diff_with_difflib, apply_diff_with_difflib_hybrid_forced
from .git_diff import use_git_to_apply_code_diff
