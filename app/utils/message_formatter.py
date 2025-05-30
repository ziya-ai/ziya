"""
Utility functions for formatting messages based on model requirements.
"""
from typing import Any, List, Dict, Union
from app.utils.logging_utils import logger

def format_message_based_on_config(message: Any, message_format: str) -> Any:
    """Format a message based on the specified format."""
    if not message_format:
        return message
        
    # Handle different message types
    if hasattr(message, 'content'):
        content = message.content
        
        # For Nova models, handle empty content specially
        if message_format == "nova":
            # If content is empty, provide a default content
            if not content:
                logger.warning(f"Empty content detected in message: {message}")
                # For Nova models, we need to filter out empty messages entirely
                logger.warning("Returning None for empty message - it will be filtered out")
                return None
            # If content is already in the right format, return as is
            elif not isinstance(content, str):
                return message
            # Format string content for Nova
            else:
                formatted_content = [{"type": "text", "text": content}]
            
            # Create a new message with the formatted content while preserving the type
            if hasattr(message, '_replace'):  # For namedtuples
                return message._replace(content=formatted_content)
            elif hasattr(message, 'copy'):  # For objects with copy method
                new_msg = message.copy()
                new_msg.content = formatted_content
                return new_msg
            else:  # For other objects
                try:
                    # Try to create a new instance of the same class
                    return type(message)(content=formatted_content)
                except Exception as e:
                    logger.error(f"Error creating new message: {e}")
                    # Fall back to modifying the original message
                    message.content = formatted_content
                    return message
    
    # Default: return the original message
    return message

def format_messages(messages: Union[List[Any], Any], message_format: str) -> Union[List[Any], Any]:
    """Format a list of messages based on the specified format."""
    if not message_format:
        return messages
        
    logger.info(f"Formatting messages using {message_format} format")
    
    # Handle different input types
    if hasattr(messages, 'to_messages'):
        # Convert ChatPromptValue to messages
        messages = list(messages.to_messages())
        logger.debug(f"Converted ChatPromptValue to {len(messages)} messages")
    elif not isinstance(messages, (list, tuple)):
        # Wrap single message in a list
        messages = [messages]
        logger.debug("Wrapped single message in a list")
    
    # Format each message
    formatted_messages = []
    for i, msg in enumerate(messages):
        # Skip None messages
        if msg is None:
            logger.warning(f"Skipping None message at index {i}")
            continue
            
        formatted_msg = format_message_based_on_config(msg, message_format)
        
        # Skip None messages (which may have been returned by format_message_based_on_config)
        if formatted_msg is None:
            logger.warning(f"Skipping message at index {i} that was converted to None")
            continue
            
        formatted_messages.append(formatted_msg)
        
        # Log the message type and content format for debugging
        if hasattr(formatted_msg, 'content'):
            content_type = type(formatted_msg.content)
            content_value = formatted_msg.content
            logger.debug(f"Message {i}: type={type(formatted_msg)}, content_type={content_type}")
            if isinstance(content_value, list) and len(content_value) > 0:
                logger.debug(f"Message {i} content: {content_value[0]}")
    
    # Ensure we have at least one message
    if not formatted_messages:
        logger.error("No valid messages after formatting!")
        # If we're left with no messages, return the original messages
        return messages
    
    # Log the final formatted messages
    logger.info(f"Final formatted messages count: {len(formatted_messages)}")
    for i, msg in enumerate(formatted_messages):
        if hasattr(msg, 'content'):
            logger.info(f"Final message {i}: role={getattr(msg, 'role', 'unknown')}, content_type={type(msg.content)}")
            if hasattr(msg, 'content') and isinstance(msg.content, list) and len(msg.content) > 0:
                logger.info(f"Final message {i} content sample: {msg.content[0]}")
        
    return formatted_messages
