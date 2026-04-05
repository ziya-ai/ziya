"""
Extended context manager for handling conversation-level extended context state.

This module manages the state of extended context usage per conversation,
ensuring that once a conversation has used extended context, it continues
to use it for subsequent requests.
"""

from typing import Dict, Optional
import time
from dataclasses import dataclass
from datetime import datetime
from app.utils.logging_utils import logger


# Bounds for per-conversation state retention
_MAX_CONTEXT_STATES = 50
_CONTEXT_STATE_TTL = 7200  # 2 hours


@dataclass
class ExtendedContextState:
    """State tracking for extended context usage in a conversation."""
    conversation_id: str
    is_using_extended: bool
    activated_at: Optional[datetime] = None
    model_name: Optional[str] = None
    original_limit: Optional[int] = None
    extended_limit: Optional[int] = None


class ExtendedContextManager:
    """Manages extended context state per conversation."""
    
    def __init__(self):
        self._conversation_states: Dict[str, ExtendedContextState] = {}
        self._access_times: Dict[str, float] = {}
        self._global_extended_context_enabled = False
        self._global_extended_context_reason = None

    def _evict_stale(self):
        """Remove expired or overflow entries."""
        now = time.time()
        expired = [
            cid for cid, t in self._access_times.items()
            if (now - t) > _CONTEXT_STATE_TTL
        ]
        for cid in expired:
            self._conversation_states.pop(cid, None)
            self._access_times.pop(cid, None)
        if expired:
            logger.info(f"♻️ ExtendedContextManager: evicted {len(expired)} expired entries")

        overflow = len(self._conversation_states) - _MAX_CONTEXT_STATES
        if overflow > 0:
            oldest = sorted(self._access_times.items(), key=lambda x: x[1])
            for cid, _ in oldest[:overflow]:
                self._conversation_states.pop(cid, None)
                self._access_times.pop(cid, None)
            logger.info(f"♻️ ExtendedContextManager: evicted {overflow} overflow entries")
    
    def is_using_extended_context(self, conversation_id: str) -> bool:
        """Check if a conversation is using extended context."""
        # Check global flag first
        if self._global_extended_context_enabled:
            return True
        
        # Check conversation-specific state
        state = self._conversation_states.get(conversation_id)
        return state.is_using_extended if state else False
    
    def activate_extended_context(
        self, 
        conversation_id: str, 
        model_name: str,
        original_limit: int,
        extended_limit: int
    ) -> str:
        """
        Activate extended context for a conversation.
        
        Returns:
            str: User notification message about extended context activation
        """
        self._evict_stale()
        self._access_times[conversation_id] = time.time()
        self._conversation_states[conversation_id] = ExtendedContextState(
            conversation_id=conversation_id,
            is_using_extended=True,
            activated_at=datetime.now(),
            model_name=model_name,
            original_limit=original_limit,
            extended_limit=extended_limit
        )
        
        logger.info(
            f"🚀 EXTENDED CONTEXT: Activated for conversation {conversation_id} "
            f"({model_name}: {original_limit:,} → {extended_limit:,} tokens)"
        )
        
        # Return user notification message
        return (
            f"🚀 **Extended Context Activated**: Your conversation has exceeded the standard "
            f"{original_limit:,} token context limit. I've automatically enabled the "
            f"{extended_limit:,} token extended context window for {model_name}. This conversation will continue "
            f"to use the extended context for all subsequent messages."
        )
    
    def get_context_limit(self, conversation_id: str, default_limit: int) -> int:
        """Get the appropriate context limit for a conversation."""
        state = self._conversation_states.get(conversation_id)
        if state and state.is_using_extended and state.extended_limit:
            return state.extended_limit
        return default_limit
    
    def should_enable_extended_context_for_files(self, file_context_size: int, standard_limit: int) -> bool:
        """Check if extended context should be enabled based on file context size."""
        # Enable extended context if file context alone is close to the standard limit
        # Use 80% of standard limit as threshold to leave room for conversation
        threshold = int(standard_limit * 0.8)
        return file_context_size > threshold
    
    def enable_extended_context_for_large_files(
        self, 
        model_name: str,
        original_limit: int,
        extended_limit: int,
        file_context_size: int
    ) -> str:
        """
        Enable extended context globally when file context is large.
        
        Returns:
            str: User notification message about extended context activation
        """
        self._global_extended_context_enabled = True
        self._global_extended_context_reason = f"Large file context: {file_context_size:,} tokens"
        
        logger.info(
            f"🚀 EXTENDED CONTEXT: Enabling globally due to large file context "
            f"({file_context_size:,} tokens, {model_name}: {original_limit:,} → {extended_limit:,} tokens)"
        )
        
        # Return user notification message
        return (
            f"🚀 **Extended Context Enabled**: Your codebase context ({file_context_size:,} tokens) "
            f"is large, so I've automatically enabled the {extended_limit:,} token extended context "
            f"window for {model_name} for all conversations."
        )
    
    def get_extended_context_state(self, conversation_id: str) -> Optional[ExtendedContextState]:
        return self._conversation_states.get(conversation_id)
    
    def clear_conversation_state(self, conversation_id: str) -> None:
        """Clear extended context state for a conversation."""
        if conversation_id in self._conversation_states:
            del self._conversation_states[conversation_id]
            self._access_times.pop(conversation_id, None)
            logger.info(f"Cleared extended context state for conversation {conversation_id}")


# Global instance
_extended_context_manager = None


def get_extended_context_manager() -> ExtendedContextManager:
    """Get the global extended context manager instance."""
    global _extended_context_manager
    if _extended_context_manager is None:
        _extended_context_manager = ExtendedContextManager()
    return _extended_context_manager
