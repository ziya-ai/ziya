"""
Performance monitoring for long conversations
"""
import time
import functools
from typing import Dict, Any, Optional
from app.utils.logging_utils import logger

class ConversationPerformanceMonitor:
    """Monitor performance metrics for conversation processing"""
    
    def __init__(self):
        self.metrics = {}
        self.conversation_sizes = {}
    
    def track_conversation_size(self, conversation_id: str, message_count: int, total_tokens: int):
        """Track the size of conversations for performance analysis"""
        self.conversation_sizes[conversation_id] = {
            'message_count': message_count,
            'total_tokens': total_tokens,
            'last_updated': time.time()
        }
        
        # Log performance warnings for large conversations
        if total_tokens > 200000:
            logger.warning(f"🐌 LARGE_CONVERSATION: {conversation_id} has {total_tokens:,} tokens ({message_count} messages)")
        elif total_tokens > 100000:
            logger.info(f"📊 CONVERSATION_SIZE: {conversation_id} has {total_tokens:,} tokens ({message_count} messages)")
    
    def time_operation(self, operation_name: str, conversation_id: str = None):
        """Decorator to time operations and log slow ones"""
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # Log slow operations
                if execution_time > 1.0:
                    conv_info = f" (conv: {conversation_id})" if conversation_id else ""
                    logger.warning(f"🐌 SLOW_OPERATION: {operation_name}{conv_info} took {execution_time:.2f}s")
                
                return result
            return wrapper
        return decorator

# Global instance
perf_monitor = ConversationPerformanceMonitor()
