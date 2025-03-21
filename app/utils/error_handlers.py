"""
Centralized error handling utilities for the application.
This module provides consistent error handling across all endpoints.
"""

import json
from typing import Dict, Any, Tuple, Optional, AsyncIterator, Callable, List
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import Request
from app.utils.logging_utils import logger

# Error type constants
ERROR_THROTTLING = "throttling_error"
ERROR_VALIDATION = "validation_error"
ERROR_AUTH = "auth_error"
ERROR_QUOTA = "quota_exceeded"
ERROR_BEDROCK = "bedrock_error"
ERROR_STREAM = "stream_error"
ERROR_SERVER = "server_error"

class ValidationError(Exception):
    """Custom exception for validation errors."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

def create_sse_error_response(error_message: str, error_type: str = ERROR_SERVER, status_code: int = 500) -> StreamingResponse:
    """
    Create a StreamingResponse with an SSE formatted error message.
    
    Args:
        error_message: The error message string
        error_type: Error type (defaults to server_error)
        status_code: HTTP status code (defaults to 500)
        
    Returns:
        StreamingResponse with the error message
    """
    async def error_stream():
        error_msg = {
            "error": error_type,
            "detail": error_message,
            "status_code": status_code
        }
        yield f"data: {json.dumps(error_msg)}\n\n"
        yield "data: [DONE]\n\n"
        
    return StreamingResponse(
        error_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )

def create_json_response(error_message: str, error_type: str = ERROR_SERVER, status_code: int = 500) -> JSONResponse:
    """
    Create a JSONResponse with an error message.
    
    Args:
        error_message: The error message string
        error_type: Error type (defaults to server_error)
        status_code: HTTP status code (defaults to 500)
        
    Returns:
        JSONResponse with the error message
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error_type,
            "detail": error_message,
            "status_code": status_code
        }
    )

def is_streaming_request(request: Request) -> bool:
    """
    Determine if a request is for a streaming response.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        bool: True if the request is for a streaming response
    """
    path = request.url.path
    return path.endswith("/stream") or "stream" in request.query_params

async def handle_streaming_error(request: Request, exc: Exception) -> AsyncIterator[str]:
    """
    General-purpose handler for streaming errors.
    This is a simplified version that handles all exceptions in a consistent way.
    
    Args:
        request: The FastAPI request object
        exc: The exception that was raised
        
    Yields:
        SSE formatted error messages
    """
    error_message = str(exc)
    logger.error(f"Handling streaming error: {error_message}")
    
    # Detect error type - simplified to avoid complex branching
    error_type = ERROR_SERVER  # Default error type
    detail = error_message
    status_code = 500
    retry_after = None
    
    # Check for common error patterns
    if "ThrottlingException" in error_message or "Too many requests" in error_message:
        error_type = ERROR_THROTTLING
        detail = "Too many requests to AWS Bedrock. Please wait a moment before trying again."
        status_code = 429
        retry_after = "5"
    elif "validationException" in error_message and "Input is too long" in error_message:
        error_type = ERROR_VALIDATION
        detail = "Selected content is too large for the model. Please reduce the number of files."
        status_code = 413
    elif ("ExpiredToken" in error_message or "InvalidIdentityToken" in error_message) and (
        "botocore" in error_message or "AWS" in error_message or "credentials" in error_message
    ):
        error_type = ERROR_AUTH
        detail = "AWS credentials have expired. Please refresh your credentials."
        status_code = 401
    elif "Resource has been exhausted" in error_message and "check quota" in error_message:
        error_type = ERROR_QUOTA
        detail = "API quota has been exceeded. Please try again in a few minutes."
        status_code = 429
        retry_after = "60"
    
    # Format the error response
    error_msg = format_error_response(error_type, detail, status_code, retry_after)
    
    # Send the error message as a properly formatted SSE message
    yield f"data: {json.dumps(error_msg)}\n\n"
    # Send the [DONE] marker
    yield "data: [DONE]\n\n"

def handle_request_exception(request: Request, exc: Exception):
    """
    General-purpose handler for all exceptions.
    
    Args:
        request: The FastAPI request object
        exc: The exception that was raised
        
    Returns:
        Either a StreamingResponse or JSONResponse depending on the request type
    """
    error_message = str(exc)
    logger.error(f"Handling exception: {error_message}")
    
    # Detect error type
    error_type = ERROR_SERVER
    detail = error_message
    status_code = 500
    retry_after = None
    
    # Check for common error patterns
    if "ThrottlingException" in error_message or "Too many requests" in error_message:
        error_type = ERROR_THROTTLING
        detail = "Too many requests to AWS Bedrock. Please wait a moment before trying again."
        status_code = 429
        retry_after = "5"
    elif "validationException" in error_message and "Input is too long" in error_message:
        error_type = ERROR_VALIDATION
        detail = "Selected content is too large for the model. Please reduce the number of files."
        status_code = 413
    elif ("ExpiredToken" in error_message or "InvalidIdentityToken" in error_message) and (
        "botocore" in error_message or "AWS" in error_message or "credentials" in error_message
    ):
        error_type = ERROR_AUTH
        detail = "AWS credentials have expired. Please refresh your credentials."
        status_code = 401
    elif "Resource has been exhausted" in error_message and "check quota" in error_message:
        error_type = ERROR_QUOTA
        detail = "API quota has been exceeded. Please try again in a few minutes."
        status_code = 429
        retry_after = "60"
    elif isinstance(exc, ValidationError):
        error_type = ERROR_VALIDATION
        detail = error_message
        status_code = 400
    
    # Return appropriate response based on request type
    if is_streaming_request(request):
        return create_sse_error_response(detail, error_type, status_code)
    else:
        return create_json_response(detail, error_type, status_code)

def format_error_response(error_type: str, detail: str, status_code: int, retry_after: Optional[str] = None) -> Dict[str, Any]:
    """
    Format an error response.
    
    Args:
        error_type: The type of error
        detail: The error message
        status_code: The HTTP status code
        retry_after: Optional retry-after value for rate limiting
        
    Returns:
        Dict with error response
    """
    error_msg = {
        "error": error_type,
        "detail": detail,
        "status_code": status_code
    }
    
    if retry_after:
        error_msg["retry_after"] = retry_after
    
    return error_msg

def is_critical_error(error_type: str) -> bool:
    """
    Determine if an error type is considered critical and should be shown to users.
    
    Args:
        error_type: The type of error to check
        
    Returns:
        bool: True if the error is critical, False otherwise
    """
    # Consider auth, quota, throttling, and bedrock errors as critical
    return error_type in [ERROR_AUTH, ERROR_QUOTA, ERROR_THROTTLING, ERROR_BEDROCK]

# Legacy function for backward compatibility
def detect_error_type(error_message: str) -> Tuple[str, str, int, Optional[str]]:
    """
    Detect the type of error from an error message.
    
    Args:
        error_message: The error message string
        
    Returns:
        Tuple of (error_type, detail, status_code, retry_after)
    """
    # Default values
    error_type = ERROR_SERVER
    detail = error_message
    status_code = 500
    retry_after = None
    
    # Check for common error patterns
    if "ThrottlingException" in error_message or "Too many requests" in error_message:
        error_type = ERROR_THROTTLING
        detail = "Too many requests to AWS Bedrock. Please wait a moment before trying again."
        status_code = 429
        retry_after = "5"
    elif "validationException" in error_message and "Input is too long" in error_message:
        error_type = ERROR_VALIDATION
        detail = "Selected content is too large for the model. Please reduce the number of files."
        status_code = 413
    elif ("ExpiredToken" in error_message or "InvalidIdentityToken" in error_message) and (
        "botocore" in error_message or "AWS" in error_message or "credentials" in error_message
    ):
        error_type = ERROR_AUTH
        detail = "AWS credentials have expired. Please refresh your credentials."
        status_code = 401
    elif "Resource has been exhausted" in error_message and "check quota" in error_message:
        error_type = ERROR_QUOTA
        detail = "API quota has been exceeded. Please try again in a few minutes."
        status_code = 429
        retry_after = "60"
    
    return error_type, detail, status_code, retry_after
