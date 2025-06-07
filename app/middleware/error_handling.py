"""
Middleware for handling errors.
"""

import json
import traceback
from typing import Dict, Any, Optional, Union
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.utils.logging_utils import logger

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware for handling errors."""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Handle errors."""
        try:
            # Call the next middleware or endpoint
            response = await call_next(request)
            return response
        except Exception as e:
            # Log the error
            logger.error(f"ErrorHandlingMiddleware caught: {str(e)}")
            
            # Check if this is a streaming request
            is_streaming = False
            for key, value in request.headers.items():
                if key.lower() == "accept" and "text/event-stream" in value.lower():
                    is_streaming = True
                    break
            
            # If this is a streaming request, we need to handle it specially
            if is_streaming:
                # Check if the response has already started
                if hasattr(request.state, "response_started") and request.state.response_started:
                    logger.error(f"Error caught after response started: {str(e)}")
                    logger.warning("Response already started. Error will be handled by lower-level ASGI middleware or client will see broken stream.")
                    # Re-raise the exception to be caught by the ASGI ErrorMiddleware,
                    # which is better equipped to send error chunks over an existing stream.
                    raise e
                
                # Create a streaming response with the error
                # Use a more descriptive error format for SSE
                async def error_stream(error_message_detail: str): # Pass detail
                    # Consistent error structure
                    error_payload = {
                        "error": "stream_error", # Or detect_error_type(error_message_detail)[0]
                        "detail": error_message_detail,
                        "status_code": 500
                    }
                    yield f"data: {json.dumps(error_payload)}\n\n"
                    yield "data: [DONE]\n\n"
                
                response = StreamingResponse(
                    error_stream(str(e)), # Pass the error detail
                    media_type="text/event-stream",
                    status_code=500
                )
                
                # Add CORS headers
                response.headers["Content-Type"] = "text/event-stream"
                response.headers["Cache-Control"] = "no-cache"
                response.headers["Connection"] = "keep-alive"
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "*"
                
                return response
            else:
                # For non-streaming requests, return a JSON response
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "server_error",
                        "detail": str(e),
                        "status_code": 500
                    }
                )
