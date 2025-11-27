"""Server module with context overflow handling."""

import re
import time
from typing import Dict, List, Any, Optional

logger = None  # Placeholder

def get_response_continuation_threshold():
    return 3400

async def check_context_overflow(
    current_response: str, 
    conversation_id: str,
    messages: List,
    full_context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Check if we're approaching context limits and need to continue in a new session.
    
    Returns:
        None if no continuation needed
        Dict with continuation info if overflow detected
    """
    # Get current model's token threshold
    token_threshold = get_response_continuation_threshold()
    
    # Estimate current response tokens
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        response_tokens = len(encoding.encode(current_response))
    except Exception:
        # Fallback: rough character-based estimation
        response_tokens = len(current_response) // 4
    
    # Check if we're approaching the model's output token limit
    if response_tokens > token_threshold:
        logger.info(f"🔄 CONTEXT: Response tokens ({response_tokens}) approaching limit ({token_threshold}), preparing continuation")
        
        # Find a good breaking point (end of sentence or paragraph)
        continuation_point = find_continuation_point(current_response)
        
        if continuation_point:
            # Find the last complete line before continuation point
            lines = current_response[:continuation_point].split('\n')
            complete_lines = lines[:-1]  # All but the potentially partial last line
            partial_last_line = lines[-1] if lines else ""
            
            completed_part = '\n'.join(complete_lines)
            
            # Add rewind marker that identifies exactly where to splice
            rewind_marker = f"\n\n<!-- REWIND_MARKER: {len(complete_lines)} -->\n**🔄 Response continues...**\n"
            completed_part += rewind_marker
            
            # Prepare continuation state
            continuation_state = {
                "rewind_line_number": len(complete_lines),
                "partial_last_line": partial_last_line,
                "rewind_marker": f"<!-- REWIND_MARKER: {len(complete_lines)} -->",
                "conversation_id": conversation_id,
                "completed_response": completed_part,
                "messages": messages,
                "context": full_context,
                "continuation_id": f"{conversation_id}_cont_{int(time.time())}"
            }
            
            return continuation_state
    
    return None

def find_continuation_point(text: str) -> Optional[int]:
    """Find an appropriate point to break the response."""
    return len(text) // 2 if len(text) > 100 else None
