"""
Core utilities for diff application.
"""

from .utils import clamp, normalize_escapes, calculate_block_similarity

# Define PatchApplicationError here since it's imported from this module
class PatchApplicationError(Exception):
    """Exception raised when a patch cannot be applied."""
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}
