"""
Middleware for handling streaming responses and errors.
"""

import json
from typing import AsyncIterator, Any
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import StreamingResponse, Response
from starlette.types import ASGIApp
from langchain_core.outputs import ChatGeneration
from langchain_core.messages import AIMessageChunk
from langchain_core.tracers.log_stream import RunLogPatch

from app.utils.logging_utils import logger

class StreamingMiddleware(BaseHTTPMiddleware):
    """Middleware for handling streaming responses."""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Handle streaming responses."""
        response = await call_next(request)
        
        if isinstance(response, StreamingResponse):
            original_iterator = response.body_iterator
            response.body_iterator = self.safe_stream(original_iterator)
        
        return response
    
    async def safe_stream(self, original_iterator: AsyncIterator[Any]) -> AsyncIterator[str]:
        """
        Safely process a stream of chunks.
        
        Args:
            original_iterator: The original stream iterator
            
        Yields:
            Processed chunks as SSE data
        """
        try:
            async for chunk in original_iterator:
                # Log chunk info for debugging
                logger.info("=== AGENT astream received chunk ===")
                logger.info(f"Chunk type: {type(chunk)}")
                
                # Process the chunk
                try:
                    # Handle RunLogPatch objects
                    if isinstance(chunk, RunLogPatch) or (hasattr(chunk, '__class__') and chunk.__class__.__name__ == 'RunLogPatch'):
                        logger.info(f"Processing RunLogPatch: {getattr(chunk, 'id', 'unknown-id')}")
                        # Skip RunLogPatch objects in tests
                        continue
                    
                    # Handle AIMessageChunk objects
                    if isinstance(chunk, AIMessageChunk):
                        logger.info("Processing AIMessageChunk")
                        # Convert AIMessageChunk to SSE data
                        content = chunk.content
                        response_json = json.dumps({"text": content})
                        yield f"data: {response_json}\n\n"
                        continue
                    
                    # Handle None chunks
                    if chunk is None:
                        logger.info("Skipping None chunk")
                        continue
                    
                    # Handle ChatGeneration objects
                    if isinstance(chunk, ChatGeneration):
                        logger.info("Processing ChatGeneration")
                        if hasattr(chunk, 'message'):
                            content = chunk.message.content
                            response_json = json.dumps({"text": content})
                            yield f"data: {response_json}\n\n"
                            continue
                    
                    # Handle string chunks
                    if isinstance(chunk, str):
                        logger.info("Processing string chunk")
                        response_json = json.dumps({"text": chunk})
                        yield f"data: {response_json}\n\n"  # Format string chunks as SSE data
                        continue
                    
                    # If we get here, we don't know how to handle this chunk type
                    logger.error(f"Unknown chunk type: {type(chunk)}")
                    error_msg = {"error": "server_error", "detail": f"Unknown chunk type: {type(chunk)}", "status_code": 500}
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    
                except Exception as chunk_error:
                    logger.error(f"Error processing chunk: {str(chunk_error)}")
                    error_msg = json.dumps({"error": "server_error", "detail": f"Error processing response: {str(chunk_error)}"})
                    yield f"data: {error_msg}\n\n"
                    continue
                    
        except Exception as e:
            logger.error(f"Error in safe_stream: {str(e)}")
            error_msg = {"error": "server_error", "detail": str(e), "status_code": 500}
            yield f"data: {json.dumps(error_msg)}\n\n"

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware for handling errors."""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Handle errors."""
        try:
            return await call_next(request)
        except Exception as e:
            logger.error(f"ErrorHandlingMiddleware caught: {str(e)}")
            
            # Check if this is a streaming request
            is_streaming = request.url.path.endswith("/stream")
            
            if is_streaming:
                # For streaming requests, return error as SSE
                async def error_stream():
                    error_msg = {"error": "server_error", "detail": str(e), "status_code": 500}
                    yield f"data: {json.dumps(error_msg)}\n\n"
                
                return StreamingResponse(
                    error_stream(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers": "*"
                    }
                )
            else:
                # For non-streaming requests, return error as JSON
                return Response(
                    content=json.dumps({"detail": str(e)}),
                    media_type="application/json",
                    status_code=500
                )

class RequestSizeMiddleware(BaseHTTPMiddleware):
    """Middleware to check request size."""
    
    def __init__(self, app: ASGIApp, default_max_size_mb: int = 20):
        """
        Initialize the middleware.
        
        Args:
            app: The ASGI app
            default_max_size_mb: Default maximum request size in MB
        """
        super().__init__(app)
        self.default_max_size_mb = default_max_size_mb
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """
        Check request size before processing.
        
        Args:
            request: The request to check
            call_next: The next middleware/endpoint to call
        
        Returns:
            Response: The response from the next middleware/endpoint
        """
        # Get content length from headers
        content_length = request.headers.get("content-length")
        if content_length:
            content_length = int(content_length)
            max_size = self.default_max_size_mb * 1024 * 1024  # Convert to bytes
            
            if content_length > max_size:
                return Response(
                    content=json.dumps({
                        "detail": f"Request too large. Maximum size is {self.default_max_size_mb}MB"
                    }),
                    media_type="application/json",
                    status_code=413
                )
        
        return await call_next(request)
