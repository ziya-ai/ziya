"""
ASGI middleware for handling errors at the lowest level of the application stack.
This ensures errors are caught before they propagate through the entire stack.
"""

import json
import traceback
from typing import Dict, Any, Optional

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import Response, JSONResponse
from app.utils.logging_utils import logger
from h11._util import LocalProtocolError
from app.utils.error_handlers import detect_error_type, format_error_response, is_critical_error
from app.utils.custom_exceptions import KnownCredentialException
from app.utils.custom_exceptions import ValidationError

class ErrorHandlingMiddleware:
    """
    ASGI middleware that catches exceptions at the lowest level and formats them
    according to our error handling standards.
    """
    
    def __init__(self, app: ASGIApp):
        self.app = app
        
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Pass through non-HTTP requests (like WebSockets)
            await self.app(scope, receive, send)
            return
        
        # Create a wrapper for the send function to track if response has started
        response_started = False
        is_streaming_response = False
        
        async def send_wrapper(message):
            nonlocal response_started, is_streaming_response
            
            try:
                await send(message)
                
                # Track response state
                if message["type"] == "http.response.start":
                    response_started = True
                    
                    # Check if this is a streaming response by looking at the Content-Type header
                    for key, value in message.get("headers", []):
                        if key.lower() == b"content-type" and b"text/event-stream" in value.lower():
                            is_streaming_response = True
                            break
                            
            except LocalProtocolError as e:
                logger.debug(f"H11 protocol error - connection broken (client likely disconnected): {e}")
                return  # Silently ignore all protocol errors when connection is broken
        
        async def safe_send(message):
            nonlocal response_started
            try:
                if message["type"] == "http.response.start":
                    await send_wrapper(message)
                    response_started = True
                else:
                    await send_wrapper(message)
            except LocalProtocolError as e:
                logger.debug(f"Protocol error during send - connection broken: {e}")
                return  # Connection is broken, silently ignore
        # Try to run the app, catch any exceptions
        try:
            await self.app(scope, receive, safe_send)
        if isinstance(exc, RequestValidationError):
            # Handle pydantic validation errors that contain credential issues
            error_message = str(exc)
            from app.plugins import get_active_auth_provider
            auth_provider = get_active_auth_provider()
            if auth_provider and auth_provider.is_auth_error(error_message):
                logger.warning(f"Credential validation error: {error_message}")
                
                # Check if this is a streaming request
                is_streaming_request = False
                for key, value in scope.get("headers", []):
                    if key.lower() == b"accept" and b"text/event-stream" in value.lower():
                        is_streaming_request = True
                        break
                        
                if is_streaming_request:
                    try:
                        await safe_send({
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [
                                (b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-cache"),
                                (b"connection", b"keep-alive"),
                                (b"access-control-allow-origin", b"*"),
                            ]
                        })
                        
                        error_content = {
                            "error": "auth_error",
                            "error_type": "authentication_error",
                            "detail": auth_provider.get_credential_help_message(),
                            "status_code": 401
                        }
                        
                        await safe_send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_content)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                        return
                    except Exception as e:
                        logger.error(f"Failed to send streaming auth error response: {e}")
                else:
                    try:
                        response = JSONResponse(
                            content={
                                "error": "auth_error",
                                "error_type": "authentication_error",
                                "detail": auth_provider.get_credential_help_message(),
                                "status_code": 401
                            },
                            status_code=401
                        )
                        await response(scope, receive, send)
                        return
                    except Exception as e:
                        logger.error(f"Failed to send JSON auth response: {e}")
            else:
                # Handle other validation errors normally
                logger.warning(f"Validation error: {error_message}")
                # Continue with existing validation error handling...
        except KnownCredentialException as exc:
            # For known credential issues, just return the message without traceback
            error_message = str(exc)
            logger.warning(f"Authentication error: {error_message}")
            
            # Check if this is a streaming request (based on Accept header)
            is_streaming_request = False
            for key, value in scope.get("headers", []):
                if key.lower() == b"accept" and b"text/event-stream" in value.lower():
                    is_streaming_request = True
                    break
                    
            # Handle the credential exception with appropriate formatting
            error_type, detail, status_code, retry_after = "auth_error", error_message, 401, None
            
            if is_streaming_request:
                # For streaming responses, send SSE format
                try:
                    # Send headers
                    await safe_send({
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"text/event-stream"),
                            (b"cache-control", b"no-cache"),
                            (b"connection", b"keep-alive"),
                            (b"access-control-allow-origin", b"*"),
                        ]
                    })
                    
                    # Send error message as SSE data
                    error_content = format_error_response(
                        error_type=error_type,
                        detail=detail,
                        status_code=status_code,
                        retry_after=retry_after
                    )
                    
                    await safe_send({
                        "type": "http.response.body",
                        "body": f"data: {json.dumps(error_content)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                        "more_body": False
                    })
                    return
                except Exception as e:
                    logger.error(f"Failed to send streaming error response: {e}")
            else:
                # For non-streaming responses, use JSONResponse
                try:
                    response = JSONResponse(
                        content=format_error_response(
                            error_type=error_type,
                            detail=detail,
                            status_code=status_code,
                            retry_after=retry_after
                        ),
                        status_code=status_code
                    )
                    await response(scope, receive, send)
                    return
                except Exception as e:
                    logger.error(f"Failed to send JSON response: {e}")
                    
        except Exception as exc:
            # Handle ValidationError specifically
            if isinstance(exc, ValidationError):
                error_message = str(exc)
                logger.warning(f"Validation error: {error_message}")
                
                # Check if this is a streaming request (based on Accept header)
                is_streaming_request = False
                for key, value in scope.get("headers", []):
                    if key.lower() == b"accept" and b"text/event-stream" in value.lower():
                        is_streaming_request = True
                        break
                        
                if is_streaming_request:
                    # For streaming responses, send SSE format
                    try:
                        # Send headers
                        await safe_send({
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [
                                (b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-cache"),
                                (b"connection", b"keep-alive"),
                                (b"access-control-allow-origin", b"*"),
                            ]
                        })
                        
                        # Send error message as SSE data
                        error_content = {
                            "error": "validation_error",
                            "detail": error_message,
                            "status_code": 413
                        }
                        
                        await safe_send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_content)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                        return
                    except Exception as e:
                        logger.error(f"Failed to send streaming validation error response: {e}")
                else:
                    # For non-streaming responses, use JSONResponse
                    try:
                        response = JSONResponse(
                            content={
                                "error": "validation_error",
                                "detail": error_message,
                                "status_code": 413
                            },
                            status_code=413
                        )
                        await response(scope, receive, send)
                        return
                    except Exception as e:
                        logger.error(f"Failed to send JSON validation response: {e}")
                        
            # Handle H11 protocol errors specifically
            if isinstance(exc, LocalProtocolError):
                logger.debug(f"H11 protocol error (client likely disconnected): {exc}")
                return  # Don't try to send a response for protocol errors
            
            error_message = str(exc)
            logger.error(f"ErrorHandlingMiddleware caught: {error_message}")
            
            # Check if this is a streaming request (based on Accept header)
            is_streaming_request = False
            for key, value in scope.get("headers", []):
                if key.lower() == b"accept" and b"text/event-stream" in value.lower():
                    is_streaming_request = True
                    break
            
            # If the response has already started, we can only send body parts
            if response_started:
                logger.warning(f"Response already started, can only send body parts (streaming: {is_streaming_response})")
                
                # For responses that have already started, we need to send a body part
                # We'll assume it's a streaming response if either:
                # 1. We detected it's a streaming response from the Content-Type header
                # 2. It was a streaming request (Accept: text/event-stream)
                if is_streaming_response or is_streaming_request:
                    try:
                        # Determine error details
                        error_type, detail, status_code, retry_after = "throttling_error", "Too many requests to AWS Bedrock. Please wait a moment before trying again.", 429, "5"
                        if "ThrottlingException" not in error_message and "Too many requests" not in error_message:
                            error_type, detail, status_code, retry_after = detect_error_type(error_message)
                        
                        # Format error as SSE
                        "event": "error",
                        "data": json.dumps({
                            "error": detail,
                            "error_type": error_type,
                            "detail": detail,
                            "status_code": status_code,
                            "retry_after": retry_after,
                            error_data["retry_after"] = retry_after
                        
                        # Send error message as SSE data
                        await safe_send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_data)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                        logger.info(f"Sent error as SSE data: {error_data}")
                    except Exception as e:
                        logger.error(f"Failed to send streaming error body: {e}")
                else:
                    # For non-streaming responses that have already started, we still need to complete the response
                    try:
                        # Send an empty body part to complete the response
                        await safe_send({
                            "type": "http.response.body",
                            "body": b"",
                            "more_body": False
                        })
                        logger.warning("Completed non-streaming response with empty body")
                    except Exception as e:
                        logger.error(f"Failed to complete non-streaming response: {e}")
                return
            
            # Handle throttling exceptions
            # Extract conversation_id from request body if available
            conversation_id = None
            try:
                if scope.get("method") == "POST":
                    # Try to extract conversation_id from request body
                    # This is a bit tricky since we're in ASGI middleware
                    # We'll need to look at the receive callable to get the body
                    pass  # Will implement body parsing below
            except Exception:
                pass
            
            # For throttling errors, try to extract conversation_id from the error message
            # or from request context if available
            if ("ThrottlingException" in error_message or "Too many requests" in error_message):
                # Check if we can extract conversation_id from scope or other context
                request_headers = dict(scope.get("headers", []))
                conversation_id_header = request_headers.get(b"x-conversation-id")
                if conversation_id_header:
                    conversation_id = conversation_id_header.decode('utf-8')
                    logger.info(f"Extracted conversation_id from headers: {conversation_id}")
                else:
                    logger.warning("Could not extract conversation_id for throttling error")
            
            if "ThrottlingException" in error_message or "Too many requests" in error_message:
                logger.info("Detected throttling error in middleware")
                
                if is_streaming_request:
                    # For streaming responses, send SSE format
                    try:
                        # Send headers
                        await safe_send({
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [
                                (b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-cache"),
                            ]
                        })
                        
                        # Send error message as SSE data
                        error_data = {
                            "error": "throttling_error",
                            "conversation_id": conversation_id,
                            "detail": "Too many requests to AWS Bedrock. Please wait a moment before trying again.",
                            "status_code": 429,
                            "retry_after": "5",
                        }
                        
                        await safe_send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_data)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                        logger.info("Sent throttling error as SSE response")
                    except Exception as e:
                        logger.error(f"Failed to send streaming error response: {e}")
                        # Fallback - try to complete the response
                        try:
                            await safe_send({
                                "type": "http.response.start",
                                "status": 500,
                                "headers": [(b"content-type", b"text/plain")]
                            })
                            await safe_send({
                                "type": "http.response.body", 
                                "body": b"",
                                "more_body": False
                            })
                        except (RuntimeError, AttributeError):
                            pass
                else:
                    # For non-streaming responses, use JSONResponse
                    try:
                        response = JSONResponse(
                            content={
                                "error": "throttling_error",
                                "conversation_id": conversation_id,
                                "detail": "Too many requests to AWS Bedrock. Please wait a moment before trying again.",
                                "status_code": 429,
                                "retry_after": "5"
                            },
                            status_code=429,
                            headers={"Retry-After": "5"}
                        )
                        await response(scope, receive, send)
                    except Exception as e:
                        logger.error(f"Failed to send JSON response: {e}")
                        # Fallback - try to complete the response
                        try:
                            await safe_send({
                                "type": "http.response.start",
                                "status": 429,
                                "headers": [
                                    (b"content-type", b"application/json"),
                                    (b"retry-after", b"5")
                                ]
                            })
                            await safe_send({
                                "type": "http.response.body",
                                "more_body": False
                            })
                        except (RuntimeError, AttributeError):
                            pass
                return
            
            # Handle validation exceptions
            if "validationException" in error_message and "Input is too long" in error_message:
                logger.info("Detected validation error in middleware - formatting as SSE")
                
                if is_streaming_request:
                    # For streaming responses, send SSE format
                    try:
                        # Send headers
                        await safe_send({
                                "type": "http.response.start",
                                "status": 200,
                                "headers": [
                                    (b"content-type", b"text/event-stream"),
                                    (b"cache-control", b"no-cache"),
                                ]
                            })
                        
                        # Send error message as SSE data
                        error_data = {
                            "error": "validation_error",
                            "detail": "Selected content is too large for the model. Please reduce the number of files.",
                            "status_code": 413
                        }
                        
                        await safe_send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_data)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                    except Exception as e:
                        logger.error(f"Failed to send streaming error response: {e}")
                else:
                    # For non-streaming responses, use JSONResponse
                    try:
                        response = JSONResponse(
                            content={
                                "error": "validation_error",
                                "detail": "Selected content is too large for the model. Please reduce the number of files.",
                                "status_code": 413
                            },
                            status_code=413
                        )
                        await response(scope, receive, send)
                    except Exception as e:
                        logger.error(f"Failed to send JSON response: {e}")
                return
            
            # Handle other validation errors that might not match the specific pattern
            if "validation" in error_message.lower() and ("too large" in error_message.lower() or "input is too long" in error_message.lower()):
                logger.info("Detected generic validation error in middleware - formatting as SSE")
                error_type = "validation_error"
                detail = "Selected content is too large for the model. Please reduce the number of files."
                status_code = 413
                retry_after = None
            else:
                # Handle other exceptions
            # Handle other exceptions
            
            # Check for validation errors in the error message itself (JSON format)
            if '"error": "validation_error"' in error_message or '"status_code": 413' in error_message:
                logger.info("Detected JSON validation error in middleware - formatting as SSE")
                
                if is_streaming_request:
                    # For streaming responses, send SSE format
                    try:
                        # Send headers
                        await safe_send({
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [
                                (b"content-type", b"text/event-stream"),
                                (b"cache-control", b"no-cache"),
                            ]
                        })
                        
                        # Send error message as SSE data
                        error_data = {
                            "error": "validation_error",
                            "detail": "Selected content is too large for the model. Please reduce the number of files.",
                            "status_code": 413
                        }
                        
                        await safe_send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_data)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                        return
                    except Exception as e:
                        logger.error(f"Failed to send streaming validation error response: {e}")
                else:
                    # For non-streaming responses, use JSONResponse
                    try:
                        response = JSONResponse(
                            content={
                                "error": "validation_error",
                                "detail": "Selected content is too large for the model. Please reduce the number of files.",
                                "status_code": 413
                            },
                            status_code=413
                        )
                        await response(scope, receive, send)
                        return
                    except Exception as e:
                        logger.error(f"Failed to send JSON validation response: {e}")
                return
            
            error_type, detail, status_code, retry_after = detect_error_type(error_message)
            
            if is_streaming_request:
                # For streaming responses, send SSE format
                try:
                    # Send headers
                    await safe_send({
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"text/event-stream"),
                            (b"cache-control", b"no-cache"),
                        ]
                    })
                    
                    # Send error message as SSE data
                    error_content = format_error_response(
                        error_type=error_type,
                        detail=detail,
                        status_code=status_code,
                        retry_after=retry_after
                    )
                    
                    await safe_send({
                        "type": "http.response.body",
                        "body": f"data: {json.dumps(error_content)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                        "more_body": False
                    })
                except Exception as e:
                    logger.error(f"Failed to send streaming error response: {e}")
            else:
                # For non-streaming responses, use JSONResponse
                try:
                    response = JSONResponse(
                        content=format_error_response(
                            error_type=error_type,
                            detail=detail,
                            status_code=status_code,
                            retry_after=retry_after
                        ),
                        status_code=status_code,
                        headers={"Retry-After": retry_after} if retry_after else None
                    )
                    await response(scope, receive, send)
                except Exception as e:
                    logger.error(f"Failed to send JSON response: {e}")
