"""
Conversation filtering utilities to prevent tool result contamination.

This module ensures that frontend display artifacts never reach the model,
preventing the model from learning to hallucinate fake tool results.
"""

from typing import List, Dict, Any, Union
from app.utils.logging_utils import logger


def filter_conversation_for_model(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter out frontend-only messages that should never reach the model.
    
    This prevents tool result contamination by excluding:
    - tool_execution (old contaminating format)
    - tool_display (new frontend-only format)
    - tool_start (frontend progress indicators)
    
    Args:
        messages: Raw conversation including frontend artifacts
        
    Returns:
        Clean conversation suitable for model consumption
    """
    filtered = []
    filtered_count = 0
    
    for msg in messages:
        if isinstance(msg, dict):
            msg_type = msg.get('type')
            
            # Skip all frontend display messages
            if msg_type in ['tool_execution', 'tool_display', 'tool_start']:
                filtered_count += 1
                logger.debug(f"Filtered out frontend artifact: {msg_type}")
                continue
                
            # Convert clean tool results for model
            elif msg_type == 'tool_result_for_model':
                filtered.append({
                    'role': 'user',
                    'content': [
                        {
                            'type': 'tool_result',
                            'tool_use_id': msg.get('tool_use_id'),
                            'content': msg.get('content', '')
                        }
                    ]
                })
                logger.debug(f"Converted tool result for model: {msg.get('tool_use_id')}")
                
            else:
                # Include regular conversation messages
                filtered.append(msg)
        else:
            # Include non-dict messages as-is
            filtered.append(msg)
    
    if filtered_count > 0:
        logger.info(f"Filtered {filtered_count} frontend artifacts from conversation")
    
    return filtered


def is_contaminating_message(msg: Union[Dict[str, Any], Any]) -> bool:
    """
    Check if a message would contaminate the model's understanding.
    
    Args:
        msg: Message to check
        
    Returns:
        True if message should be filtered out
    """
    if not isinstance(msg, dict):
        return False
        
    msg_type = msg.get('type')
    contaminating_types = [
        'tool_execution',      # Old contaminating format
        'tool_display',        # New frontend-only format  
        'tool_start',          # Progress indicators
        'tool_progress',       # Progress updates
        'stream_end',          # Stream control
        'error'                # Error displays
    ]
    
    return msg_type in contaminating_types
