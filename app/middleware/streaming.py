"""
Middleware for handling streaming responses and errors.
"""

import os
import json
from typing import AsyncIterator, Any
from app.agents.wrappers.nova_formatter import NovaFormatter
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import StreamingResponse, Response
from starlette.types import ASGIApp
from langchain_core.outputs import ChatGeneration
from langchain_core.messages import AIMessage
from langchain_core.messages import AIMessageChunk
from langchain_core.tracers.log_stream import RunLogPatch

from app.utils.logging_utils import logger

class StreamingMiddleware(BaseHTTPMiddleware):
    """Middleware for handling streaming responses."""
    
    # Class-level variables for repetition detection
    _recent_lines = []
    _max_repetitions = 10
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
    
    async def dispatch(
              self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Handle streaming responses."""
        # Check if this is a streaming request
        is_streaming = False
        for key, value in request.headers.items():
            if key.lower() == "accept" and "text/event-stream" in value.lower():
                is_streaming = True
                break
        
        # If this is a streaming request, we need to handle it specially
        if is_streaming and "/ziya/stream" in request.url.path:
            logger.info(f"Detected streaming request to {request.url.path}")
            # For streaming requests, we need to modify the response
            response = await call_next(request)
            
            if isinstance(response, StreamingResponse):
                # Replace the body iterator with our safe stream
                original_iterator = response.body_iterator
                response.body_iterator = self.safe_stream(original_iterator)
                logger.info("Applied safe_stream to streaming response")
            
            return response
        else:
            # For non-streaming requests, just pass through
            return await call_next(request)
    
    def _is_repetitive(self, content: str) -> bool:
        """Check if content contains repetitive lines that exceed threshold."""
        return any(content.count(line) > self._max_repetitions for line in set(content.split('\n')) if line.strip())
    
    async def safe_stream(self, original_iterator: AsyncIterator[Any]) -> AsyncIterator[str]:
        """
        Safely process a stream of chunks.
        
        Args:
            original_iterator: The original stream iterator
            
        Yields:
            Processed chunks as SSE data
        """
        # Reset repetition detection state for this stream
        self._recent_lines = []
        accumulated_content = ""
        try:
            async for chunk in original_iterator:
                # Log chunk info for debugging
                logger.info("=== AGENT astream received chunk ===")
                logger.info(f"Chunk type: {type(chunk)}")
                # Log thinking mode status
                thinking_mode_enabled = os.environ.get("ZIYA_THINKING_MODE") == "1"
                logger.debug(f"Thinking mode enabled: {thinking_mode_enabled}")
                
                chunk_content = ""
                # Process the chunk
                try:
                    # Handle AIMessageChunk objects
                    if isinstance(chunk, AIMessageChunk):
                        logger.info("Processing AIMessageChunk")
                    
                    # Get the raw content, preserving structure
                    raw_content = chunk.content
                    
                    # Check if this might be thinking mode content (typically more structured)
                    chunk_content = raw_content
                    is_structured = isinstance(raw_content, dict) and ('thinking' in raw_content or 'reasoning' in raw_content)
                    logger.debug(f"Content appears to be structured thinking: {is_structured}")
                    
                        # Let stream_chunks handle structured error detection within content
                    if raw_content: # Avoid sending empty data chunks
                            # Properly handle different content types
                        if isinstance(raw_content, dict):
                            # For structured content like thinking mode, preserve the structure
                            logger.debug(f"Preserving structured content: {list(raw_content.keys())}")
                            yield f"data: {json.dumps(raw_content)}\n\n"
                            chunk_content = json.dumps(raw_content)
                        else:
                            # For simple string content
                            content = str(raw_content)
                            yield f"data: {content}\n\n"
                        continue
                    
                    # Handle None chunks
                    if chunk is None:
                        logger.info("Skipping None chunk")
                        continue
                    
                    # Handle ChatGeneration objects
                    if isinstance(chunk, ChatGeneration):
                        logger.info("Processing ChatGeneration")
                        if hasattr(chunk, 'message'):
                            content = chunk.message.content
                            if content:
                                chunk_content = content
                                if isinstance(content, dict):
                                    # Preserve structured content
                                    logger.debug(f"Preserving structured ChatGeneration content: {list(content.keys()) if isinstance(content, dict) else 'non-dict'}")
                                    yield f"data: {json.dumps(content)}\n\n"
                                elif isinstance(content, list) and all(isinstance(item, dict) for item in content):
                                    yield f"data: {json.dumps(content)}\n\n"
                                else:
                                    yield f"data: {content}\n\n"
                                continue
                    
                    # Handle RunLogPatch objects - convert to SSE data
                    if isinstance(chunk, RunLogPatch) or (hasattr(chunk, '__class__') and chunk.__class__.__name__ == 'RunLogPatch'):
                        logger.info(f"Processing RunLogPatch")
                        # Extract content from RunLogPatch if possible
                        if hasattr(chunk, 'ops') and chunk.ops:
                            for op in chunk.ops:
                                if op.get('op') == 'add' and 'value' in op and 'content' in op['value']:
                                    content = op['value']['content']
                                    if isinstance(content, dict):
                                        chunk_content = json.dumps(content)
                                        yield f"data: {json.dumps(content)}\n\n"
                                    else:
                                        yield f"data: {content}\n\n"
                                yield f"data: {content}\n\n"
                                continue
                    
                    # Handle DeepSeek format specifically
                    if isinstance(chunk, dict) and "generation" in chunk:
                        logger.info("Processing DeepSeek generation chunk")
                        content = chunk["generation"]
                        if content:
                            chunk_content = content
                            yield f"data: {content}\n\n"
                            continue
                    elif isinstance(raw_content, dict) and "generation" in raw_content:
                        yield f"data: {raw_content['generation']}\n\n"
                        continue
                    
                    # Handle string chunks
                    if isinstance(chunk, str):
                        logger.info("Processing string chunk")
                        # Check if it's already an SSE message
                        if chunk.startswith('data:'):
                            yield chunk
                        else:
                            # Check if it might be JSON
                            try:
                                # Try to parse as JSON to validate
                                json_obj = json.loads(chunk)
                                # If it's valid JSON, pass it through as properly serialized JSON
                                chunk_content = json.dumps(json_obj)
                                yield f"data: {json.dumps(json_obj)}\n\n"
                            except json.JSONDecodeError:
                                # If it's not valid JSON, just pass it as a string
                                yield f"data: {chunk}\n\n"
                        
                        # Log chunk content preview
                        if len(chunk) > 200:
                            logger.info(f"String chunk preview:\n{chunk[:200]}...")
                            logger.info(f"...and ends with:\n{chunk[-200:]}")
                        else:
                            logger.info(f"Full string chunk:\n{chunk}")
                        continue
                    
                    # If we get here, try to extract content from the chunk
                    if hasattr(chunk, 'content'):
                        logger.info("Extracting content from chunk")
                        content = chunk.content
                        if callable(content):
                            content = content()
                        if content:
                            if isinstance(content, dict):
                                yield f"data: {json.dumps(content)}\n\n"
                                chunk_content = json.dumps(content)
                            else:
                                yield f"data: {content}\n\n"
                            continue
                    
                    # Last resort: convert to string
                    logger.info("Converting chunk to string")
                    str_chunk = str(chunk)
                    chunk_content = str_chunk
                    if str_chunk: # Avoid empty data chunks
                        # Check for repetitive content
                        accumulated_content += str_chunk
                        
                        # Track lines for repetition detection
                        lines = str_chunk.split('\n')
                        for line in lines:
                            if line.strip():  # Only track non-empty lines
                                self._recent_lines.append(line)
                                # Keep only recent lines
                                if len(self._recent_lines) > 100:
                                    self._recent_lines.pop(0)
                        
                        # Check if any line repeats too many times
                        if any(self._recent_lines.count(line) > self._max_repetitions for line in set(self._recent_lines)):
                            logger.warning("Detected repetitive content in stream, interrupting")
                            # Send warning message
                            warning_msg = {
                                "warning": "repetitive_content",
                                "detail": "Response was interrupted because repetitive content was detected."
                            }
                            yield f"data: {json.dumps(warning_msg)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        else:
                            yield f"data: {str_chunk}\n\n"
                    
                except Exception as chunk_error:
                    logger.error(f"Error processing chunk: {str(chunk_error)}")
                    # Check if this looks like a JSON error message that should be formatted as SSE
                    
                    # Handle the case where we get a validation error as plain JSON
                    if isinstance(chunk, str) and '"error": "validation_error"' in chunk:
                        logger.info("Converting validation error JSON to SSE format")
                        try:
                            # Extract the JSON part
                            json_start = chunk.find('{"error"')
                            json_end = chunk.find('}', json_start) + 1
                            if json_start >= 0 and json_end > json_start:
                                error_json = chunk[json_start:json_end]
                                error_data = json.loads(error_json)
                                yield f"data: {json.dumps(error_data)}\n\n"
                                yield "data: [DONE]\n\n"
                                return
                        except Exception as e:
                            logger.warning(f"Failed to convert validation error to SSE: {e}")
                    
                    chunk_str = str(chunk)
                    if chunk_str.startswith('{"error":'):
                        # This is an error response that needs proper SSE formatting
                        try:
                            # Find where the JSON ends - look for various patterns
                            if 'data: [DONE]' in chunk_str:
                                json_part = chunk_str.split('data: [DONE]')[0].strip()
                            elif '}data:' in chunk_str:
                                json_part = chunk_str.split('}data:')[0] + '}'
                            else:
                                # Look for the end of the JSON object
                                brace_count = 0
                                json_end = 0
                                for i, char in enumerate(chunk_str):
                                    if char == '{':
                                        brace_count += 1
                                    elif char == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            json_end = i + 1
                                            break
                                json_part = chunk_str[:json_end] if json_end > 0 else chunk_str
                                
                            # Clean up any trailing characters that aren't part of JSON
                            json_part = json_part.rstrip()
                            error_data = json.loads(json_part)
                            yield f"data: {json.dumps(error_data)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse error JSON: {json_part}")
                    
                    # Fallback: treat as regular chunk processing error
                    error_msg = {"error": "chunk_processing_error", "detail": str(chunk_error)}
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                    
            # Ensure we end the stream properly
            try:
                # Send the [DONE] marker
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"Error sending DONE marker: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error in safe_stream: {str(e)}")
            # Send error as SSE data
            error_msg = {
                "error": "stream_processing_error",
                "detail": str(e)
            }
            yield f"data: {json.dumps(error_msg)}\n\n"
            try:
                yield "data: [DONE]\n\n"
            except Exception as done_error:
                logger.error(f"Error sending final DONE marker: {str(done_error)}")
                # Don't re-raise here as it would cause more protocol errors
                pass
