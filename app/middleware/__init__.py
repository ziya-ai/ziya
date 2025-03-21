"""
Middleware package for the Ziya application.
"""

from app.middleware.request_size import RequestSizeLimiter as RequestSizeMiddleware
from app.middleware.error_middleware import ErrorHandlingMiddleware

# Export both the new name and the old name for backward compatibility
__all__ = ['RequestSizeMiddleware', 'RequestSizeLimiter', 'ErrorHandlingMiddleware']

# Alias for backward compatibility
RequestSizeLimiter = RequestSizeMiddleware
