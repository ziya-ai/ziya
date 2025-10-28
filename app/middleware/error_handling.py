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
from h11._util import LocalProtocolError

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
        except LocalProtocolError as e:
            # Connection is already in ERROR state, can't send more data
            logger.error(f"h11 protocol error (connection already closed): {str(e)}")
            return Response(status_code=500)
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
                    logger.warning("Response already started, can only send body parts (streaming: True)")
                    # Create an error message
                    preserved_content = None
                    if hasattr(e, 'response_metadata') and e.response_metadata.get('has_preserved_content'):
                        preserved_content = e.response_metadata.get('preserved_content')
                    
                    error_msg = {
                        "error": "stream_error",
                        "detail": str(e),
                        "status_code": 500
                    }
                    
                    if preserved_content:
                        error_msg.update({
                            "has_preserved_content": True,
                            "preserved_content": preserved_content
                        })
                    
                    logger.info(f"Sent error as SSE data: {error_msg}")
                    # We can't do anything here, the response has already started
                    return Response(status_code=500, content=json.dumps(error_msg))
                
                # Create a streaming response with the error
                # Use a more descriptive error format for SSE
                async def error_stream(error_message):
                    yield f"data: Error: {str(e)}\n\n"
                    yield "data: [DONE]\n\n"
                
                response = StreamingResponse(
                    error_stream(),
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
