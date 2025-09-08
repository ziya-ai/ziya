"""
Minimal throttling handler for AWS Bedrock requests.
"""

import asyncio
import json
from typing import AsyncGenerator, Dict, Any
from app.utils.logging_utils import logger

async def handle_with_backoff(stream_func, *args, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
    """Execute streaming function with exponential backoff for throttling errors."""
    backoff_delays = [5, 10, 20, 40]
    max_retries = len(backoff_delays)
    
    for retry_count in range(max_retries + 1):
        try:
            async for chunk in stream_func(*args, **kwargs):
                yield chunk
            return  # Success
            
        except Exception as e:
            error_str = str(e)
            
            # Check if this is a throttling error
            if any(indicator in error_str for indicator in [
                "ThrottlingException", "Too many requests", "Rate exceeded", 
                "Throttling", "throttling", "TooManyRequestsException"
            ]):
                if retry_count < max_retries:
                    delay = backoff_delays[retry_count]
                    logger.warning(f"Throttling detected, retry {retry_count + 1}/{max_retries + 1} after {delay}s")
                    
                    # Send status message to frontend
                    yield {
                        'type': 'throttling_status',
                        'retry_count': retry_count + 1,
                        'max_retries': max_retries + 1,
                        'delay': delay,
                        'message': f"AWS Bedrock throttling. Retrying in {delay}s... (Attempt {retry_count + 1}/{max_retries + 1})"
                    }
                    
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Max retries exceeded
                    yield {
                        'type': 'throttling_failed',
                        'message': "Maximum retry attempts exceeded. Please try again later.",
                        'show_continue_button': True
                    }
                    return
            else:
                # Not a throttling error, re-raise
                raise
