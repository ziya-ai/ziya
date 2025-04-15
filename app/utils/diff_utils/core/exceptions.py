"""
Exceptions for diff utilities.
"""

class PatchApplicationError(Exception):
    """
    Exception raised when a patch cannot be applied.
    
    Attributes:
        message -- explanation of the error
        details -- additional details about the error
    """
    
    def __init__(self, message, details=None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)
