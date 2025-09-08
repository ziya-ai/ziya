"""
Custom Bedrock client wrapper that ensures max_tokens is correctly passed to the API.
"""

import json
import os
import re
import gc
from app.utils.logging_utils import logger
from app.utils.extended_context_manager import get_extended_context_manager
from app.utils.conversation_context import get_conversation_id
from typing import Dict, List, Optional

# Module global to store current conversation_id (workaround for thread boundary issues)
_current_conversation_id: Optional[str] = None

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
    
    def __init__(self, client, max_tokens=None, model_config=None):
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
        self.last_extended_context_notification = None
        self.model_config = model_config or {}
        self.extended_context_manager = get_extended_context_manager()
        
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
    
    def _supports_extended_context(self) -> bool:
        """Check if the current model supports extended context."""
        # Cache the result to avoid repeated logging
        if not hasattr(self, '_cached_supports_extended_context'):
            supports = self.model_config.get("supports_extended_context", False)
            logger.info(f"🔍 EXTENDED_CONTEXT: Model config supports_extended_context = {supports}")
            logger.info(f"🔍 EXTENDED_CONTEXT: Model config keys = {list(self.model_config.keys())}")
            self._cached_supports_extended_context = supports
        return self._cached_supports_extended_context
    
    def _get_extended_context_header(self) -> Optional[str]:
        """Get the extended context header for the current model."""
        return self.model_config.get("extended_context_header")
    
    def _get_context_limits(self) -> tuple[int, int]:
        """Get the standard and extended context limits."""
        standard_limit = self.model_config.get("token_limit", self.CLAUDE_CONTEXT_LIMIT)
        extended_limit = self.model_config.get("extended_context_limit", standard_limit)
        return standard_limit, extended_limit
    
    def _extract_conversation_id_from_request(self, kwargs: Dict) -> Optional[str]:
        """Extract conversation_id from the request context."""
        # First try to get from module global (workaround for thread boundary issues)
        global _current_conversation_id
        if _current_conversation_id:
            logger.info(f"🔍 EXTENDED_CONTEXT: Found conversation_id in module global: {_current_conversation_id}")
            return _current_conversation_id
        
        # Try to get from thread-local context
        conversation_id = get_conversation_id()
        if conversation_id:
            logger.info(f"🔍 EXTENDED_CONTEXT: Found conversation_id in thread-local: {conversation_id}")
            return conversation_id
        
        logger.info("🔍 EXTENDED_CONTEXT: No conversation_id found")
        return None
    
    def _should_use_extended_context(self, conversation_id: Optional[str] = None) -> bool:
        """Determine if extended context should be used for this conversation."""
        if not self._supports_extended_context():
            return False
        
        # Check if extended context is globally enabled
        if self.extended_context_manager._global_extended_context_enabled:
            return True
        
        # Check conversation-specific state
        if conversation_id:
            return self.extended_context_manager.is_using_extended_context(conversation_id)
        
        return False
    
    def _add_extended_context_headers(self, kwargs: Dict, conversation_id: Optional[str] = None) -> Dict:
        """Add extended context headers to the request if needed."""
        if self._should_use_extended_context(conversation_id):
            header_value = self._get_extended_context_header()
            if header_value:
                # For streaming API, we need to add the header to the request body
                if 'body' in kwargs and isinstance(kwargs['body'], str):
                    try:
                        body_dict = json.loads(kwargs['body'])
                        # Add anthropic_beta to the request body
                        body_dict['anthropic_beta'] = [header_value]
                        kwargs['body'] = json.dumps(body_dict)
                        logger.debug(f"Added extended context header to body: anthropic_beta=[{header_value}]")
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Failed to add extended context header to body: {e}")
                else:
                    # For non-streaming API, use additionalModelRequestFields
                    if 'additionalModelRequestFields' not in kwargs:
                        kwargs['additionalModelRequestFields'] = {}
                    kwargs['additionalModelRequestFields']['anthropic_beta'] = [header_value]
                    logger.debug(f"Added extended context header: anthropic_beta=[{header_value}]")
        
        return kwargs
    
    def _retry_with_extended_context(self, kwargs: Dict, error_message: str, conversation_id: Optional[str] = None):
        """Retry the request with extended context enabled."""
        if not conversation_id or not self._supports_extended_context():
            raise Exception(error_message)
        
        # Activate extended context for this conversation
        standard_limit, extended_limit = self._get_context_limits()
        model_name = self.model_config.get("model_id", {}).get("us", "unknown")
        
        notification_message = self.extended_context_manager.activate_extended_context(
            conversation_id,
            model_name,
            standard_limit,
            extended_limit
        )
        
        # Add extended context headers
        kwargs = self._add_extended_context_headers(kwargs, conversation_id)
        
        # Store the notification message for later retrieval
        self.last_extended_context_notification = notification_message
        
        # Notify user about extended context activation
        logger.info(
            f"🚀 EXTENDED CONTEXT: Retrying with {extended_limit:,} token context window for {model_name} "
            f"(conversation {conversation_id})"
        )
        
        # Retry the request with extended context headers
        logger.info(f"🚀 EXTENDED_CONTEXT: About to retry streaming call with extended context")
        
        try:
            result = self.original_invoke(**kwargs)
            logger.info(f"🚀 EXTENDED_CONTEXT: Streaming retry completed successfully")
            return result
        except Exception as retry_error:
            retry_error_str = str(retry_error)
            logger.error(f"🚀 EXTENDED_CONTEXT: Retry failed with error: {retry_error}")
            
            # If it's still a validation error or connection error, convert to a user-friendly message
            if ("Input is too long" in retry_error_str or 
                "Connection was closed" in retry_error_str or
                "ValidationException" in retry_error_str):
                logger.error("Extended context retry failed - content may be too large even for extended context")
                raise Exception("The selected content is too large even for extended context. Please reduce the number of files or select smaller files.")
            else:
                raise retry_error
    
    def _create_custom_invoke_streaming(self):
        """Create a custom implementation of invoke_model_with_response_stream."""
        def custom_invoke(**kwargs):
            logger.debug(f"CustomBedrockClient.invoke_model_with_response_stream called with kwargs keys: {list(kwargs.keys())}")
            
            # Extract conversation_id from request context
            conversation_id = self._extract_conversation_id_from_request(kwargs)
            logger.info(f"🔍 EXTENDED_CONTEXT: Extracted conversation_id = {conversation_id}")
            
            # Add extended context headers if needed
            kwargs = self._add_extended_context_headers(kwargs, conversation_id)
            
            # If body is in kwargs, modify it to include max_tokens
            if 'body' in kwargs and isinstance(kwargs['body'], str):
                try:
                    # First attempt with user-configured max_tokens
                    adjusted_body = self._prepare_request_body(kwargs['body'], kwargs.get('modelId', ''))
                    kwargs['body'] = adjusted_body
                    
                    try:
                        # Try the API call
                        return self.original_invoke(**kwargs)
                    except Exception as e:
                        error_message = str(e)
                        self.last_error = error_message
                        
                        # Check if it's a context limit error
                        if ("input length and `max_tokens` exceed context limit" in error_message or
                            "Input is too long" in error_message):
                            logger.warning(f"Context limit error detected: {error_message}")
                            
                            # Try extended context if supported and not already using it
                            if (self._supports_extended_context() and 
                                conversation_id and 
                                not self._should_use_extended_context(conversation_id)):
                                
                                return self._retry_with_extended_context(kwargs, error_message, conversation_id)
                            
                            # Otherwise, try standard context reduction
                            limit_info = self._extract_context_limit_info(error_message)
                            if limit_info:
                                # Calculate a safe max_tokens value
                                current_limit = (self.extended_context_manager.get_context_limit(
                                    conversation_id, self.CLAUDE_CONTEXT_LIMIT) 
                                    if conversation_id else self.CLAUDE_CONTEXT_LIMIT)
                                
                                safe_max_tokens = self._calculate_safe_max_tokens(
                                    limit_info["input_tokens"],
                                    current_limit
                                )
                                
                                # Adjust the request body with the safe max_tokens
                                body_dict = json.loads(kwargs['body'])
                                original_max_tokens = body_dict.get("max_tokens", "not set")
                                body_dict["max_tokens"] = safe_max_tokens
                                kwargs['body'] = json.dumps(body_dict)
                                
                                logger.info(f"Retrying with adjusted max_tokens: {original_max_tokens} -> {safe_max_tokens}")
                                
                                # Retry with adjusted parameters
                                return self.original_invoke(**kwargs)
                        
                        # Re-raise the original exception if we can't handle it
                        raise
                except Exception as e:
                    # Log more details about the error
                    logger.error(f"Error in custom invoke: {e}")
                    if 'modelId' in kwargs:
                        logger.error(f"Model ID being used: {kwargs['modelId']}")
                    
                    # For streaming calls, let StreamingToolExecutor handle extended context
                    # to avoid breaking async context
                    logger.info("🔍 EXTENDED_CONTEXT: Skipping CustomBedrockClient retry for streaming - letting StreamingToolExecutor handle it")
                    raise
                    error_message = str(e)
                    logger.info(f"🔍 EXTENDED_CONTEXT: Checking error for extended context retry: {error_message[:100]}...")
                    logger.info(f"🔍 EXTENDED_CONTEXT: conversation_id = {conversation_id}")
                    logger.info(f"🔍 EXTENDED_CONTEXT: supports_extended_context = {self._supports_extended_context()}")
                    logger.info(f"🔍 EXTENDED_CONTEXT: should_use_extended_context = {self._should_use_extended_context(conversation_id) if conversation_id else 'N/A'}")
                    
                    if (("Input is too long" in error_message or 
                         "input length and `max_tokens` exceed context limit" in error_message) and
                        self._supports_extended_context() and 
                        conversation_id and 
                        not self._should_use_extended_context(conversation_id)):
                        
                        logger.info("🚀 EXTENDED_CONTEXT: Attempting retry with extended context")
                        try:
                            return self._retry_with_extended_context(kwargs, error_message, conversation_id)
                        except Exception as retry_error:
                            logger.error(f"Extended context retry failed: {retry_error}")
                    else:
                        logger.info("🔍 EXTENDED_CONTEXT: Extended context retry conditions not met")
                    
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
            
            # Extract conversation_id from request context
            conversation_id = self._extract_conversation_id_from_request(kwargs)
            
            # Add extended context headers if needed
            kwargs = self._add_extended_context_headers(kwargs, conversation_id)
            
            # If body is in kwargs, modify it to include max_tokens
            if 'body' in kwargs and isinstance(kwargs['body'], str):
                try:
                    # First attempt with user-configured max_tokens
                    adjusted_body = self._prepare_request_body(kwargs['body'], kwargs.get('modelId', ''))
                    kwargs['body'] = adjusted_body
                    
                    try:
                        # Try the API call
                        return self.original_invoke_model(**kwargs)
                    except Exception as e:
                        error_message = str(e)
                        self.last_error = error_message
                        
                        # Check if it's a context limit error
                        if ("input length and `max_tokens` exceed context limit" in error_message or
                            "Input is too long" in error_message):
                            logger.warning(f"Context limit error detected: {error_message}")
                            
                            # Try extended context if supported and not already using it
                            if (self._supports_extended_context() and 
                                conversation_id and 
                                not self._should_use_extended_context(conversation_id)):
                                
                                # Activate extended context and retry
                                standard_limit, extended_limit = self._get_context_limits()
                                model_name = self.model_config.get("model_id", {}).get("us", "unknown")
                                
                                self.extended_context_manager.activate_extended_context(
                                    conversation_id,
                                    model_name,
                                    standard_limit,
                                    extended_limit
                                )
                                
                                # Add extended context headers
                                kwargs = self._add_extended_context_headers(kwargs, conversation_id)
                                
                                logger.info(
                                    f"🚀 EXTENDED CONTEXT: Retrying with {extended_limit:,} token context window for {model_name} "
                                    f"(conversation {conversation_id})"
                                )
                                
                                return self.original_invoke_model(**kwargs)
                            
                            # Otherwise, try standard context reduction
                            limit_info = self._extract_context_limit_info(error_message)
                            if limit_info:
                                # Calculate a safe max_tokens value
                                current_limit = (self.extended_context_manager.get_context_limit(
                                    conversation_id, self.CLAUDE_CONTEXT_LIMIT) 
                                    if conversation_id else self.CLAUDE_CONTEXT_LIMIT)
                                
                                safe_max_tokens = self._calculate_safe_max_tokens(
                                    limit_info["input_tokens"],
                                    current_limit
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
    
    def _prepare_request_body(self, body_str, model_id=''):
        """Prepare the request body with the appropriate max_tokens value."""
        try:
            body_dict = json.loads(body_str)
            
            # Get the effective max_tokens value to use
            effective_max_tokens = self._get_effective_max_tokens()
            
            # Only set max_tokens if it's not already in the body
            if 'max_tokens' not in body_dict and 'maxTokens' not in body_dict and effective_max_tokens is not None:
                # Check if this is a Nova Pro model (has different parameter requirements)
                if 'nova-pro' in model_id.lower():
                    # Nova Pro doesn't support token limit parameters, skip adding them
                    logger.debug(f"Skipping token limit for Nova Pro model: {model_id}")
                elif 'nova-micro' in model_id.lower():
                    # Nova Micro models don't accept max_tokens parameter
                    logger.debug(f"Skipping token limit for Nova Micro model: {model_id}")
                else:
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
