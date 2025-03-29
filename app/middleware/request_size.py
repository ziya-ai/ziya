"""
Middleware for limiting request size.
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.utils.logging_utils import logger

class RequestSizeMiddleware(BaseHTTPMiddleware):
    """Middleware for limiting request size."""
    
    def __init__(self, app: ASGIApp, default_max_size_mb: int = 10):
        super().__init__(app)
        self.default_max_size_mb = default_max_size_mb
        self.max_size = default_max_size_mb * 1024 * 1024  # Convert to bytes
        logger.info(f"RequestSizeLimiter initialized with default {default_max_size_mb}MB limit")
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Limit request size."""
        # Get content length from headers
        content_length = request.headers.get("content-length")
        
        # If content length is provided, check if it exceeds the limit
        if content_length:
            content_length = int(content_length)
            if content_length > self.max_size:
                logger.warning(f"Request size {content_length} exceeds limit {self.max_size}")
                return Response(
                    content=f"Request size {content_length} exceeds limit {self.max_size}",
                    status_code=413,
                    media_type="text/plain"
                )
        
        # Call the next middleware or endpoint
        return await call_next(request)
