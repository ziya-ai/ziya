"""
Custom wrapper for Bedrock client to handle exceptions gracefully.
"""

import json
from typing import Dict, Any, Optional, List, Iterator, AsyncIterator
from botocore.exceptions import ClientError, EventStreamError
from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessageChunk, AIMessage
from app.utils.logging_utils import logger

class ThrottleSafeBedrock(ChatBedrock):
    """
    A wrapper around ChatBedrock that catches exceptions and logs them,
    but doesn't try to handle them directly.
    """
    
    def _stream(self, *args, **kwargs) -> Iterator:
        """Override the _stream method to catch exceptions."""
        try:
            return super()._stream(*args, **kwargs)
        except ClientError as e:
            error_message = str(e)
            logger.warning(f"Caught ClientError in _stream: {error_message}")
            
            # Check if this is a throttling error
            if "ThrottlingException" in error_message or "Too many requests" in error_message:
                logger.warning("Detected throttling error, returning empty list")
                # Return an empty list
                return []
            
            # For other client errors, re-raise
            raise
        except EventStreamError as e:
            error_message = str(e)
            logger.warning(f"Caught EventStreamError in _stream: {error_message}")
            
            # Check if this is a validation error
            if "validationException" in error_message and "Input is too long" in error_message:
                logger.warning("Detected validation error, returning empty list")
                # Return an empty list
                return []
            
            # For other event stream errors, re-raise
            raise
        except Exception as e:
            error_str = str(e)
            logger.error(f"Unexpected error in _stream: {error_str}")
            raise

    async def _astream(self, *args, **kwargs) -> AsyncIterator:
        """Override the _astream method to catch exceptions."""
        try:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
        except ClientError as e:
            error_message = str(e)
            logger.warning(f"Caught ClientError in _astream: {error_message}")
            
            # Check if this is a throttling error
            if "ThrottlingException" in error_message or "Too many requests" in error_message:
                logger.warning("Detected throttling error, not yielding anything")
                # Don't yield anything, just return
                return
            
            # For other client errors, re-raise
            raise
        except EventStreamError as e:
            error_message = str(e)
            logger.warning(f"Caught EventStreamError in _astream: {error_message}")
            
            # Check if this is a validation error
            if "validationException" in error_message and "Input is too long" in error_message:
                logger.warning("Detected validation error, not yielding anything")
                # Don't yield anything, just return
                return
            
            # For other event stream errors, re-raise
            raise
        except Exception as e:
            error_str = str(e)
            logger.error(f"Unexpected error in _astream: {error_str}")
            raise
