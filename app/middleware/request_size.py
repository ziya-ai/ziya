"""
Middleware for limiting request size based on model configuration.
"""

import json
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse, StreamingResponse
from app.utils.logging_utils import logger

# Import configuration instead of ModelManager
import app.config as config

class RequestSizeLimiter(BaseHTTPMiddleware):
    """
    Middleware to limit the size of incoming requests based on model configuration.
    
    This middleware checks the Content-Length header and rejects requests that exceed
    the configured maximum size. The size limit is determined by the model configuration
    and can be disabled for certain models (e.g., Claude on Bedrock).
    """
    
    def __init__(self, app, default_max_size_mb=config.DEFAULT_MAX_REQUEST_SIZE_MB):
        super().__init__(app)
        self.default_max_size_mb = default_max_size_mb
        self.default_max_size_bytes = default_max_size_mb * 1024 * 1024
        logger.info(f"RequestSizeLimiter initialized with default {default_max_size_mb}MB limit")
    
    async def dispatch(self, request: Request, call_next):
        # Get current model config
        endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", config.DEFAULT_MODELS.get(endpoint))
        
        # For version and fbuild commands, we don't need to check size limits
        if request.url.path == "/api/version" or "fbuild" in os.environ.get("ZIYA_COMMAND", ""):
            return await call_next(request)
        
        # For actual model operations, we need the full ModelManager
        from app.agents.models import ModelManager
        
        # Get complete model configuration with inheritance
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # Check if size limits should be enforced for this model
        enforce_size_limit = model_config.get("enforce_size_limit", False)
        if not enforce_size_limit:
            logger.debug(f"Size limits disabled for model {model_name} on {endpoint}")
            logger.debug(f"Size limits disabled for {endpoint}/{model_name}, skipping size check")
            return await call_next(request)
        
        # Get max size from config or use default
        max_size_mb = model_config.get("max_request_size_mb", self.default_max_size_mb)
        max_size_bytes = max_size_mb * 1024 * 1024
        
        logger.debug(f"Checking request size with limit of {max_size_mb}MB for {endpoint}/{model_name}")
        
        # Check content length header
        content_length = request.headers.get('content-length')
        if content_length:
            cl = int(content_length)
            if cl > max_size_bytes:
                logger.warning(f"Request size too large: {cl} bytes (limit: {max_size_bytes} bytes)")
                
                # Import here to avoid circular imports
                from app.utils.error_handlers import ValidationError
                raise ValidationError(f"Request body too large. Maximum size is {max_size_mb}MB for {model_name}.")
        
        # Continue processing the request
        return await call_next(request)

# Alias for backward compatibility
RequestSizeMiddleware = RequestSizeLimiter
