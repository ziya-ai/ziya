"""
State management utilities for notifying frontend about processing states.
"""

import asyncio
import json
from typing import Dict, Any, Optional
from app.utils.logging_utils import logger

# Global state tracking
_active_conversations: Dict[str, Dict[str, Any]] = {}
_state_callbacks: Dict[str, callable] = {}

async def notify_processing_state(
    state: str, 
    details: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None
):
    """
    Notify frontend about processing state changes.
    
    Args:
        state: The processing state ('awaiting_tool_response', 'tool_throttling', etc.)
        details: Additional details about the state
        conversation_id: The conversation ID (if available)
    """
    try:
        # Create state update message
        state_update = {
            "type": "processing_state_update",
            "state": state,
            "details": details or {},
            "timestamp": asyncio.get_event_loop().time()
        }
        
        if conversation_id:
            state_update["conversation_id"] = conversation_id
            
        # Log the state change
        logger.info(f"🔄 Processing state update: {state} - {details}")
        
        # TODO: Implement actual notification mechanism to frontend
        # This could be through SSE, WebSocket, or response metadata
        
    except Exception as e:
        logger.error(f"Error notifying processing state: {e}")
