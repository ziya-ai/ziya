"""
Middleware for tracking and broadcasting hunk application status.
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import StreamingResponse, JSONResponse
from starlette.types import ASGIApp
from app.utils.logging_utils import logger

class HunkStatusMiddleware(BaseHTTPMiddleware):
    """Middleware for tracking and broadcasting hunk application status."""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        logger.info("HunkStatusMiddleware initialized")
        self.hunk_statuses = {}
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Track hunk status updates from apply-patch responses."""
        response = await call_next(request)
        
        # Check if this is a response from the apply-patch endpoint
        if request.url.path == "/api/apply-changes" and request.method == "POST":
            try:
                # Handle different response types
                if isinstance(response, StreamingResponse):
                    logger.info("HunkStatusMiddleware: Detected StreamingResponse, can't process directly")
                    # For streaming responses, we can't modify them directly
                    # The event system will handle this instead
                    return response
                elif isinstance(response, JSONResponse):
                    try:
                        # For JSONResponse, we can access the body directly
                        body = response.body
                        
                        # Parse the JSON
                        import json
                        data = json.loads(body)
                        
                        # If the response contains hunk statuses, store them
                        if "details" in data and "hunk_statuses" in data["details"]:
                            logger.error(f"Error processing JSONResponse: {str(e)}", exc_info=True)
                    except Exception as e:
                        logger.error(f"Error processing JSONResponse: {str(e)}", exc_info=True)
            except Exception as e:
                logger.error(f"Error in HunkStatusMiddleware: {str(e)}", exc_info=True)
        return response

