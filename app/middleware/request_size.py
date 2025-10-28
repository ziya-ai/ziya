"""
Middleware for limiting request size.
"""

from fastapi import Request, Response
import json
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp
from starlette.responses import JSONResponse
import os
from app.utils.logging_utils import logger
# Middleware for handling request size limits and model settings

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
            
        # Call the next middleware or endpoint only if size check passes
        return await call_next(request)

class ModelSettingsMiddleware(BaseHTTPMiddleware):
    """Middleware for ensuring model settings are properly applied."""
    
    _hunk_statuses = {}  # Class-level storage for hunk statuses by request ID
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        logger.info("ModelSettingsMiddleware initialized")
    
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Ensure model settings are properly applied."""
        # Check if this is a model settings update request
        if request.url.path == "/api/model-settings" and request.method == "POST":
            try:
                # Get the request body
                logger.info("Processing model settings update")
                    
                body = await request.json()
                # Clear any existing model-specific settings from environment
                
                # Log the settings being applied
                logger.info(f"ModelSettingsMiddleware: Applying settings: {body}")
                
                # Ensure max_output_tokens is correctly set in environment
                if "max_output_tokens" in body:
                    max_output_tokens = body["max_output_tokens"]
                    os.environ["ZIYA_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
                    # Also set ZIYA_MAX_TOKENS to ensure it's used by the model
                    os.environ["ZIYA_MAX_TOKENS"] = str(max_output_tokens)
                    logger.info(f"ModelSettingsMiddleware: Set ZIYA_MAX_TOKENS={max_output_tokens}")
                
                # Set max_input_tokens in environment if provided
                if "max_input_tokens" in body:
                    max_input_tokens = body["max_input_tokens"]
                    os.environ["ZIYA_MAX_INPUT_TOKENS"] = str(max_input_tokens)
                    
                    # Handle top_k parameter - check if it's supported by the current model
                    if "top_k" in body:
                        from app.agents.models import ModelManager
                        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                        model_name = os.environ.get("ZIYA_MODEL")
                        
                        # Get model configuration
                        model_config = ModelManager.get_model_config(endpoint, model_name)
                        supported_params = []
                        
                        # Check if model supports top_k
                        if 'supported_parameters' in model_config and 'top_k' in model_config['supported_parameters']:
                            os.environ["ZIYA_TOP_K"] = str(body["top_k"])
                            logger.info(f"ModelSettingsMiddleware: Set ZIYA_TOP_K={body['top_k']}")
                        elif "ZIYA_TOP_K" in os.environ:
                            # If not supported, remove from environment
                            if os.environ.get("ZIYA_TOP_K"):
                                del os.environ["ZIYA_TOP_K"]
                                logger.info("ModelSettingsMiddleware: Removed ZIYA_TOP_K as it's not supported by current model")
                        
                    # Handle thinking_mode parameter
                    if "thinking_mode" in body:
                        from app.agents.models import ModelManager
                        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                        model_name = os.environ.get("ZIYA_MODEL")
                        
                        # Get model configuration
                        model_config = ModelManager.get_model_config(endpoint, model_name)
                        
                        # Check if model supports thinking mode
                        supports_thinking = model_config.get("supports_thinking", False)
                        thinking_mode = body["thinking_mode"]
                        
                        if supports_thinking:
                            os.environ["ZIYA_THINKING_MODE"] = "1" if thinking_mode else "0"
                            logger.info(f"ModelSettingsMiddleware: Set ZIYA_THINKING_MODE={thinking_mode}")
                        else:
                            # If not supported, always set to 0
                            os.environ["ZIYA_THINKING_MODE"] = "0"
                            logger.info("ModelSettingsMiddleware: Set ZIYA_THINKING_MODE=0 (not supported by current model)")
                        
                        # Force model reinitialization by clearing the model from ModelManager state
                        from app.agents.models import ModelManager
                        ModelManager._reset_state()
                        logger.info(f"ModelSettingsMiddleware: Reset model state to force reinitialization with settings: {json.dumps(body)}")
            except Exception as e:
                logger.error(f"ModelSettingsMiddleware error: {str(e)}")
                
        # Call the next middleware or endpoint
        return await call_next(request)
