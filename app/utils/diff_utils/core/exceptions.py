"""
Exceptions used throughout the diff_utils package.
"""

from typing import Dict

class PatchApplicationError(Exception):
    """Custom exception for patch application failures"""
    def __init__(self, message: str, details: Dict):
        super().__init__(message)
        self.details = details
