"""
Core utilities for the diff_utils package.

This module provides the core functionality used throughout the diff_utils package,
including exceptions, utility functions, and common data structures.
"""

from .exceptions import PatchApplicationError
from .utils import clamp, normalize_escapes, calculate_block_similarity
