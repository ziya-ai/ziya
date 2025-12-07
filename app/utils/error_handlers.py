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

def _handle_aws_credential_error(error_message):
    """Handle AWS credential errors with appropriate messages."""
    error_type = ERROR_AUTH
    
    # Get help from active auth provider
    from app.plugins import get_active_auth_provider
    auth_provider = get_active_auth_provider()
    
    if auth_provider:
        detail = auth_provider.get_credential_help_message()
    else:
        detail = "AWS credentials have expired. Please refresh your credentials."
        
    return error_type, detail, 401, None

class ValidationError(Exception):
    """Custom exception for validation errors."""
    pass


def is_streaming_request(request: Request) -> bool:
    """Check if the request is for a streaming response."""
    return request.headers.get("accept") == "text/event-stream"


def create_json_response(
    error_type: str, detail: str, status_code: int = 500, headers: Dict[str, str] = None
) -> JSONResponse:
    """Create a JSON response for errors."""
    content = {"error": {"type": error_type, "detail": detail}}
    return JSONResponse(content=content, status_code=status_code, headers=headers)


def create_sse_error_response(error_type: str, detail: str) -> Dict[str, Any]:
    """Create a Server-Sent Events (SSE) error response."""
    # Ensure the detail is a string to avoid serialization issues
    if not isinstance(detail, str):
        detail = str(detail)
    
    return {"error": {"type": error_type, "detail": detail}}


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
    if ("ThrottlingException" in error_message or 
        "Too many requests" in error_message or 
        ("reached max retries" in error_message and "ThrottlingException" in error_message)):
        error_type = ERROR_THROTTLING
        
        # Check if this indicates exhausted retries
        if "reached max retries" in error_message:
            detail = "AWS Bedrock rate limit exceeded. All automatic retries have been exhausted. You can try again now, or wait 1-2 minutes for better success rate."
            retry_after = "60"
        else:
            detail = "Too many requests to AWS Bedrock. The system will automatically retry."
            retry_after = "5"
            
        status_code = 429
        
    elif "validationException" in error_message and "Input is too long" in error_message:
        error_type = ERROR_VALIDATION
        detail = "Selected content is too large for the model. Please reduce the number of files."
        status_code = 413
    elif ("ExpiredToken" in error_message or "InvalidIdentityToken" in error_message or "InvalidClientTokenId" in error_message) and (
        "botocore" in error_message or "AWS" in error_message or "credentials" in error_message):
        error_type, detail, status_code, retry_after = _handle_aws_credential_error(error_message)
    elif "CredentialRetrievalError" in error_message or "You may need to authenticate" in error_message:
        error_type, detail, status_code, retry_after = _handle_aws_credential_error(error_message)
    elif "Resource has been exhausted" in error_message and "check quota" in error_message:
        error_type = ERROR_QUOTA
        detail = "API quota has been exceeded. Please try again in a few minutes."
        status_code = 429
        retry_after = "60"
    elif "model_id" in error_message and "not found" in error_message:
        error_type = ERROR_BEDROCK
        detail = "The selected model is not available. Please try a different model."
        status_code = 404
    elif "AccessDeniedException" in error_message:
        error_type = ERROR_AUTH
        detail = "Access denied. Your AWS credentials don't have sufficient permissions to use this model."
        status_code = 403
    
    # Format the error response
    error_response = create_sse_error_response(error_type, detail)
    
    # Convert to SSE format
    yield f"data: {json.dumps(error_response)}\n\n"
    yield "event: close\ndata: \n\n"


def handle_request_exception(request: Request, exc: Exception) -> JSONResponse:
    """
    General-purpose handler for request exceptions.
    
    Args:
        request: The FastAPI request object
        exc: The exception that was raised
        
    Returns:
        JSONResponse with appropriate error details
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
    elif ("ExpiredToken" in error_message or "InvalidIdentityToken" in error_message or "InvalidClientTokenId" in error_message) and (
        "botocore" in error_message or "AWS" in error_message or "credentials" in error_message
    ):
        error_type, detail, status_code, retry_after = _handle_aws_credential_error(error_message)
    elif "Resource has been exhausted" in error_message and "check quota" in error_message:
        error_type = ERROR_QUOTA
        detail = "API quota has been exceeded. Please try again in a few minutes."
        status_code = 429
        retry_after = "60"
    elif "model_id" in error_message and "not found" in error_message:
        error_type = ERROR_BEDROCK
        detail = "The selected model is not available. Please try a different model."
        status_code = 404
    elif "AccessDeniedException" in error_message:
        error_type = ERROR_AUTH
        detail = "Access denied. Your AWS credentials don't have sufficient permissions to use this model."
        status_code = 403
    
    # Create headers if needed
    headers = {}
    if retry_after:
        headers["Retry-After"] = retry_after
    
    return create_json_response(error_type, detail, status_code, headers)


def detect_error_type(error_message: str) -> Tuple[str, str, int, Optional[str]]:
    """
    Detect the type of error from an error message.
    
    Args:
        error_message: The error message to analyze
        
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
    elif ("ExpiredToken" in error_message or "InvalidIdentityToken" in error_message or "InvalidClientTokenId" in error_message) and (
        "botocore" in error_message or "AWS" in error_message or "credentials" in error_message
    ):
        error_type, detail, status_code, retry_after = _handle_aws_credential_error(error_message)
    elif "Resource has been exhausted" in error_message and "check quota" in error_message:
        error_type = ERROR_QUOTA
        detail = "API quota has been exceeded. Please try again in a few minutes."
        status_code = 429
        retry_after = "60"
    elif "model_id" in error_message and "not found" in error_message:
        error_type = ERROR_BEDROCK
        detail = "The selected model is not available. Please try a different model."
        status_code = 404
    elif "AccessDeniedException" in error_message:
        error_type = ERROR_AUTH
        detail = "Access denied. Your AWS credentials don't have sufficient permissions to use this model."
        status_code = 403
    
    return error_type, detail, status_code, retry_after


def format_error_response(error_type: str, detail: str, status_code: int = 500, retry_after: Optional[str] = None) -> Dict[str, Any]:
    """Format an error response for the API."""
    # Ensure detail is a string to avoid serialization issues
    if not isinstance(detail, str):
        detail = str(detail)
    
    error_response = {
        "error": error_type,
        "detail": detail,
        "status_code": status_code
    }
    
    if retry_after:
        error_response["retry_after"] = retry_after
        
    return error_response


def is_critical_error(error_message: str) -> bool:
    """Check if an error is critical and should terminate the stream."""
    return any(
        pattern in error_message
        for pattern in [
            "ExpiredToken",
            "InvalidIdentityToken",
            "AccessDeniedException",
            "ThrottlingException",
            "Too many requests",
            "Resource has been exhausted",
        ]
    )
