"""
Exceptions for the diff_utils package.
"""

class PatchApplicationError(Exception):
    """
    Exception raised when a patch cannot be applied.

    Attributes:
        message -- explanation of the error
        details -- structured diagnostic payload (dict) produced by the
                   apply pipeline; consumed by the CLI renderer and the
                   low-confidence diagnostic path.  Must exist as a real
                   attribute so ``except ... as e: e.details`` works.
    """

    def __init__(self, message, details=None):
        self.message = message
        self.details = details if isinstance(details, dict) else {}
        super().__init__(self.message)
