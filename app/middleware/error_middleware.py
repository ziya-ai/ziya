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
from app.utils.error_handlers import detect_error_type, format_error_response, is_critical_error
from app.utils.custom_exceptions import KnownCredentialException

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
            
            # Check if this is a response start message
            if message["type"] == "http.response.start":
                response_started = True
                
                # Check if this is a streaming response by looking at the Content-Type header
                for key, value in message.get("headers", []):
                    if key.lower() == b"content-type" and b"text/event-stream" in value.lower():
                        is_streaming_response = True
                        break
            
            await send(message)
        
        # Try to run the app, catch any exceptions
        try:
            await self.app(scope, receive, send_wrapper)
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
                    await send({
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
                    
                    await send({
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
                        status_code=status_code
                    )
                    await response(scope, receive, send)
                except Exception as e:
                    logger.error(f"Failed to send JSON response: {e}")
                    
        except Exception as exc:
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
                        error_data = {
                            "error": error_type,
                            "detail": detail,
                            "status_code": status_code
                        }
                        if retry_after:
                            error_data["retry_after"] = retry_after
                        
                        # Send error message as SSE data
                        await send({
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
                        await send({
                            "type": "http.response.body",
                            "body": b"",
                            "more_body": False
                        })
                        logger.warning("Completed non-streaming response with empty body")
                    except Exception as e:
                        logger.error(f"Failed to complete non-streaming response: {e}")
                return
            
            # Handle throttling exceptions
            if "ThrottlingException" in error_message or "Too many requests" in error_message:
                logger.info("Detected throttling error in middleware")
                
                if is_streaming_request:
                    # For streaming responses, send SSE format
                    try:
                        # Send headers
                        await send({
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
                            "detail": "Too many requests to AWS Bedrock. Please wait a moment before trying again.",
                            "status_code": 429,
                            "retry_after": "5"
                        }
                        
                        await send({
                            "type": "http.response.body",
                            "body": f"data: {json.dumps(error_data)}\n\ndata: [DONE]\n\n".encode('utf-8'),
                            "more_body": False
                        })
                        logger.info("Sent throttling error as SSE response")
                    except Exception as e:
                        logger.error(f"Failed to send streaming error response: {e}")
                        # Fallback - try to complete the response
                        try:
                            await send({
                                "type": "http.response.body",
                                "body": b"",
                                "more_body": False
                            })
                        except:
                            pass
                else:
                    # For non-streaming responses, use JSONResponse
                    try:
                        response = JSONResponse(
                            content={
                                "error": "throttling_error",
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
                            await send({
                                "type": "http.response.start",
                                "status": 500,
                                "headers": [(b"content-type", b"text/plain")]
                            })
                            await send({
                                "type": "http.response.body",
                                "body": b"Internal Server Error",
                                "more_body": False
                            })
                        except:
                            pass
                return
            
            # Handle validation exceptions
            if "validationException" in error_message and "Input is too long" in error_message:
                logger.info("Detected validation error in middleware")
                
                if is_streaming_request:
                    # For streaming responses, send SSE format
                    try:
                        # Send headers
                        await send({
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
                        
                        await send({
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
            
            # Handle other exceptions
            error_type, detail, status_code, retry_after = detect_error_type(error_message)
            
            if is_streaming_request:
                # For streaming responses, send SSE format
                try:
                    # Send headers
                    await send({
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
                    
                    await send({
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
