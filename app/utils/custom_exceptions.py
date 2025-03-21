"""
Custom exceptions for the application.
"""

class ThrottlingException(Exception):
    """
    Exception raised when AWS Bedrock returns a throttling error.
    This is a custom exception that can be caught and handled properly.
    """
    def __init__(self, message="Too many requests to AWS Bedrock. Please wait a moment before trying again."):
        self.message = message
        super().__init__(self.message)
        
    def __str__(self):
        return self.message
        
class ExpiredTokenException(Exception):
    """
    Exception raised when AWS credentials have expired.
    """
    def __init__(self, message="AWS credentials have expired. Please refresh your credentials."):
        self.message = message
        super().__init__(self.message)
        
    def __str__(self):
        return self.message
