"""
Custom Bedrock client wrapper that ensures max_tokens is correctly passed to the API.
"""

import json
import os
import re
import gc
from app.utils.logging_utils import logger
from typing import Dict, List

class CustomBedrockClient:
    """
    Custom Bedrock client that intercepts the invoke_model_with_response_stream method.
    This ensures max_tokens is correctly passed to the API.
    
    The key issue is that the Bedrock API for Claude models has a context limit that includes
    both the input tokens and the max_tokens value. When the sum exceeds the model's context limit,
    we get an error like:
    
    "input length and `max_tokens` exceed context limit: 179563 + 64000 > 204698"
    
    This wrapper ensures that max_tokens is properly set in the request body and dynamically
    adjusts it if needed to avoid context limit errors.
    """
    
    # Constants for Claude models
    CLAUDE_CONTEXT_LIMIT = 204698  # Based on observed error messages
    CLAUDE_SAFETY_MARGIN = 1000    # Safety margin to avoid edge cases
    
    def __init__(self, client, max_tokens=None):
        """Initialize the custom client."""
        # Force garbage collection to clean up any lingering references
        gc.collect()
        
        # Use the provided client directly instead of creating a new one
        # This preserves the ability to create fresh clients when needed while
        # avoiding unnecessary client creation during normal operation
        self.client = client
        
        self.user_max_tokens = max_tokens
        self.default_max_tokens = 4000  # Default fallback if not specified
        self.last_error = None
        
        # Get the region from the client
        self.region = self.client.meta.region_name if hasattr(self.client, 'meta') else None
        logger.info(f"Initialized CustomBedrockClient with user_max_tokens={max_tokens}, region={self.region}")

        # Store region in environment variable to ensure consistency
        if self.region:
            os.environ["AWS_REGION"] = self.region
        
        # Store the original methods
        self.original_invoke = self.client.invoke_model_with_response_stream
        if hasattr(self.client, 'invoke_model'):
            self.original_invoke_model = self.client.invoke_model
        
        # Replace the streaming method with our custom implementation
        self.invoke_model_with_response_stream = self._create_custom_invoke_streaming()
        
        # Replace the non-streaming method if it exists
        if hasattr(self.client, 'invoke_model'):
            self.invoke_model = self._create_custom_invoke_non_streaming()
    
    def _create_custom_invoke_streaming(self):
        """Create a custom implementation of invoke_model_with_response_stream."""
        def custom_invoke(**kwargs):
            logger.debug(f"CustomBedrockClient.invoke_model_with_response_stream called with kwargs keys: {list(kwargs.keys())}")
            
            # If body is in kwargs, modify it to include max_tokens
            if 'body' in kwargs and isinstance(kwargs['body'], str):
                try:
                    # First attempt with user-configured max_tokens
                    adjusted_body = self._prepare_request_body(kwargs['body'])
                    kwargs['body'] = adjusted_body
                    
                    try:
                        # Try the API call
                        return self.original_invoke(**kwargs)
                    except Exception as e:
                        error_message = str(e)
                        self.last_error = error_message
                        
                        # Check if it's a context limit error
                        if "input length and `max_tokens` exceed context limit" in error_message:
                            logger.warning(f"Context limit error detected: {error_message}")
                            
                            # Extract context limit information
                            limit_info = self._extract_context_limit_info(error_message)
                            if limit_info:
                                # Calculate a safe max_tokens value
                                safe_max_tokens = self._calculate_safe_max_tokens(
                                    limit_info["input_tokens"],
                                    limit_info["context_limit"]
                                )
                                
                                # Adjust the request body with the safe max_tokens
                                body_dict = json.loads(kwargs['body'])
                                original_max_tokens = body_dict.get("max_tokens", "not set")
                                body_dict["max_tokens"] = safe_max_tokens
                                kwargs['body'] = json.dumps(body_dict)
                                
                                logger.info(f"Retrying with adjusted max_tokens: {original_max_tokens} -> {safe_max_tokens}")
                                
                                # Retry with adjusted parameters
                                return self.original_invoke(**kwargs)
                        
                        # Fall back to original method if our customization fails
                        return self.original_invoke(**kwargs)
                except Exception as e:
                    # Log more details about the error
                    logger.error(f"Error in custom invoke: {e}")
                    if 'modelId' in kwargs:
                        logger.error(f"Model ID being used: {kwargs['modelId']}")
                    
                    # Try to create a regular bedrock client to list models
                    try:
                        import boto3
                        bedrock_client = boto3.client('bedrock', region_name=self.region)
                        logger.info(f"Available models in region {self.region}: {[m['modelId'] for m in bedrock_client.list_foundation_models()['modelSummaries']]}")
                    except Exception as list_error:
                        logger.error(f"Error listing models: {list_error}")
                        
                    logger.error(f"Error in custom invoke: {e}")
                    # Fall back to original method if our customization fails
                    return self.original_invoke(**kwargs)
            else:
                # If no body or not a string, just call the original method
                return self.original_invoke(**kwargs)
        
        return custom_invoke
    
    def _create_custom_invoke_non_streaming(self):
        """Create a custom implementation of invoke_model."""
        def custom_invoke_model(**kwargs):
            logger.debug(f"CustomBedrockClient.invoke_model called with kwargs keys: {list(kwargs.keys())}")
            
            # If body is in kwargs, modify it to include max_tokens
            if 'body' in kwargs and isinstance(kwargs['body'], str):
                try:
                    # First attempt with user-configured max_tokens
                    adjusted_body = self._prepare_request_body(kwargs['body'])
                    kwargs['body'] = adjusted_body
                    
                    try:
                        # Try the API call
                        return self.original_invoke_model(**kwargs)
                    except Exception as e:
                        error_message = str(e)
                        self.last_error = error_message
                        
                        # Check if it's a context limit error
                        if "input length and `max_tokens` exceed context limit" in error_message:
                            logger.warning(f"Context limit error detected: {error_message}")
                            
                            # Extract context limit information
                            limit_info = self._extract_context_limit_info(error_message)
                            if limit_info:
                                # Calculate a safe max_tokens value
                                safe_max_tokens = self._calculate_safe_max_tokens(
                                    limit_info["input_tokens"],
                                    limit_info["context_limit"]
                                )
                                
                                # Adjust the request body with the safe max_tokens
                                body_dict = json.loads(kwargs['body'])
                                original_max_tokens = body_dict.get("max_tokens", "not set")
                                body_dict["max_tokens"] = safe_max_tokens
                                kwargs['body'] = json.dumps(body_dict)
                                
                                logger.info(f"Retrying with adjusted max_tokens: {original_max_tokens} -> {safe_max_tokens}")
                                
                                # Retry with adjusted parameters
                                return self.original_invoke_model(**kwargs)
                        
                        # Re-raise the original exception if we can't handle it
                        raise
                except Exception as e:
                    logger.error(f"Error in custom invoke_model: {e}")
                    # Fall back to original method if our customization fails
                    return self.original_invoke_model(**kwargs)
            else:
                # If no body or not a string, just call the original method
                return self.original_invoke_model(**kwargs)
        
        return custom_invoke_model
    
    def _prepare_request_body(self, body_str):
        """Prepare the request body with the appropriate max_tokens value."""
        try:
            body_dict = json.loads(body_str)
            
            # Get the effective max_tokens value to use
            effective_max_tokens = self._get_effective_max_tokens()
            
            # Only set max_tokens if it's not already in the body
            if 'max_tokens' not in body_dict and effective_max_tokens is not None:
                body_dict['max_tokens'] = effective_max_tokens
                logger.debug(f"Added max_tokens={effective_max_tokens} to request body")

            # Handle context caching parameters
            if 'messages' in body_dict:
                has_cache_control = self._process_cache_control(body_dict['messages'])
                
                # Add contextTtlInSeconds if we have cache control
                if has_cache_control and 'contextTtlInSeconds' not in body_dict:
                    body_dict['contextTtlInSeconds'] = 3600  # 1 hour default
                    logger.info(f"🕐 CACHE: Set TTL to 3600 seconds (1 hour)")
            
            return json.dumps(body_dict)
        except Exception as e:
            logger.error(f"Error preparing request body: {e}")
            return body_str

    def _process_cache_control(self, messages: List[Dict]) -> bool:
        """Process cache control parameters in messages. Returns True if any cache control was found."""
        has_cache_control = False
        
        for message in messages:
            if isinstance(message, dict):
                # Check for cache_control in additional_kwargs (LangChain format)
                if 'additional_kwargs' in message and isinstance(message['additional_kwargs'], dict):
                    cache_control = message['additional_kwargs'].get('cache_control')
                    if cache_control:
                        message['cache_control'] = cache_control
                        has_cache_control = True
                        logger.info(f"🏷️  CACHE: Applied cache control: {cache_control}")
        
        return has_cache_control
    
    def _get_effective_max_tokens(self):
        """Get the effective max_tokens value to use, considering environment variables."""
        # Check if there's an environment variable override
        env_max_tokens = os.environ.get("ZIYA_MAX_OUTPUT_TOKENS")
        if env_max_tokens:
            try:
                return int(env_max_tokens)
            except ValueError:
                logger.warning(f"Invalid ZIYA_MAX_OUTPUT_TOKENS value: {env_max_tokens}")
        
        # Fall back to the user-configured value or default
        return self.user_max_tokens if self.user_max_tokens is not None else self.default_max_tokens
    
    def _extract_context_limit_info(self, error_message):
        """Extract context limit information from an error message."""
        # Pattern to match: "input length and `max_tokens` exceed context limit: 179563 + 64000 > 204698"
        pattern = r"input length and `max_tokens` exceed context limit: (\d+) \+ (\d+) > (\d+)"
        match = re.search(pattern, error_message)
        
        if match:
            return {
                "input_tokens": int(match.group(1)),
                "max_tokens": int(match.group(2)),
                "context_limit": int(match.group(3))
            }
        return None
    
    def _calculate_safe_max_tokens(self, input_tokens, context_limit=CLAUDE_CONTEXT_LIMIT):
        """Calculate a safe max_tokens value based on input size and context limit."""
        # Calculate available tokens for output
        available_tokens = context_limit - input_tokens - self.CLAUDE_SAFETY_MARGIN
        
        # Ensure we have at least 1000 tokens for output
        min_output_tokens = 1000
        safe_max_tokens = max(available_tokens, min_output_tokens)
        
        # Cap at a reasonable maximum (e.g., 32K)
        max_reasonable_tokens = 32000
        safe_max_tokens = min(safe_max_tokens, max_reasonable_tokens)
        
        return safe_max_tokens
    
    def __getattr__(self, name):
        """Forward all other attributes to the original client."""
        return getattr(self.client, name)
