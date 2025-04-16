"""
Custom Bedrock client wrapper that ensures max_tokens is correctly passed to the API.
"""

import json
from app.utils.logging_utils import logger

class CustomBedrockClient:
    """
    Custom Bedrock client that intercepts the invoke_model_with_response_stream method.
    This ensures max_tokens is correctly passed to the API.
    """
    
    def __init__(self, client, max_tokens=None):
        """Initialize the custom client."""
        self.client = client
        self.max_tokens = max_tokens
        logger.info(f"Initialized CustomBedrockClient with max_tokens={max_tokens}")
        
        # Store the original method
        self.original_invoke = client.invoke_model_with_response_stream
        
        # Replace the method with our custom implementation
        def custom_invoke(**kwargs):
            logger.debug(f"CustomBedrockClient.invoke_model_with_response_stream called with kwargs keys: {list(kwargs.keys())}")
            
            # If body is in kwargs, modify it to include max_tokens
            if 'body' in kwargs and isinstance(kwargs['body'], str):
                try:
                    body_dict = json.loads(kwargs['body'])
                    logger.debug(f"Original request body max_tokens: {body_dict.get('max_tokens')}")
                    
                    # Set max_tokens if it's not already set
                    if 'max_tokens' not in body_dict and self.max_tokens is not None:
                        body_dict['max_tokens'] = self.max_tokens
                        logger.info(f"Added max_tokens={self.max_tokens} to request body")
                    elif body_dict.get('max_tokens') != self.max_tokens and self.max_tokens is not None:
                        logger.info(f"Updated max_tokens from {body_dict.get('max_tokens')} to {self.max_tokens}")
                        body_dict['max_tokens'] = self.max_tokens
                    
                    # Update the body in kwargs
                    kwargs['body'] = json.dumps(body_dict)
                    logger.debug(f"Modified request body max_tokens: {body_dict.get('max_tokens')}")
                except Exception as e:
                    logger.error(f"Error modifying request body: {e}")
            
            # Call the original method
            return self.original_invoke(**kwargs)
        
        # Replace the method
        self.invoke_model_with_response_stream = custom_invoke
        
        # Also handle the non-streaming invoke_model method if it exists
        if hasattr(client, 'invoke_model'):
            self.original_invoke_model = client.invoke_model
            
            def custom_invoke_model(**kwargs):
                logger.debug(f"CustomBedrockClient.invoke_model called with kwargs keys: {list(kwargs.keys())}")
                
                # If body is in kwargs, modify it to include max_tokens
                if 'body' in kwargs and isinstance(kwargs['body'], str):
                    try:
                        body_dict = json.loads(kwargs['body'])
                        logger.debug(f"Original request body max_tokens: {body_dict.get('max_tokens')}")
                        
                        # Set max_tokens if it's not already set
                        if 'max_tokens' not in body_dict and self.max_tokens is not None:
                            body_dict['max_tokens'] = self.max_tokens
                            logger.info(f"Added max_tokens={self.max_tokens} to request body")
                        elif body_dict.get('max_tokens') != self.max_tokens and self.max_tokens is not None:
                            logger.info(f"Updated max_tokens from {body_dict.get('max_tokens')} to {self.max_tokens}")
                            body_dict['max_tokens'] = self.max_tokens
                        
                        # Update the body in kwargs
                        kwargs['body'] = json.dumps(body_dict)
                        logger.debug(f"Modified request body max_tokens: {body_dict.get('max_tokens')}")
                    except Exception as e:
                        logger.error(f"Error modifying request body: {e}")
                
                # Call the original method
                return self.original_invoke_model(**kwargs)
            
            # Replace the method
            self.invoke_model = custom_invoke_model
    
    def __getattr__(self, name):
        """Forward all other attributes to the original client."""
        return getattr(self.client, name)
