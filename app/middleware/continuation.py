#!/usr/bin/env python3
"""
Context Continuation Middleware

Handles seamless continuation when approaching output context limits.
"""

import asyncio
import json
import re
import time
from typing import Dict, Any, Optional, AsyncGenerator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.utils.logging_utils import logger


class ContinuationMiddleware(BaseHTTPMiddleware):
    """Middleware to handle context continuation for streaming responses."""
    
    def __init__(self, app):
        super().__init__(app)
        self.active_streams = {}
    
    async def dispatch(self, request: Request, call_next):
        """Process request and handle continuation if needed."""
        
        # Only apply to streaming endpoints
        if not self._is_streaming_endpoint(request):
            return await call_next(request)
        
        try:
            response = await call_next(request)
            
            # Wrap streaming responses with continuation detection
            if isinstance(response, StreamingResponse):
                wrapped_generator = self._wrap_streaming_response(
                    response.body_iterator, 
                    request
                )
                return StreamingResponse(
                    wrapped_generator,
                    media_type=response.media_type,
                    headers=response.headers
                )
            
            return response
            
        except Exception as e:
            logger.error(f"Error in continuation middleware: {e}")
            return await call_next(request)
    
    def _get_response_threshold(self) -> int:
        """Get the response threshold based on current model settings."""
        try:
            from app.agents.models import ModelManager
            model_settings = ModelManager.get_model_settings()
            max_output_tokens = model_settings.get("max_output_tokens", 4096)
            
            # Use 85% of configured limit
            return int(max_output_tokens * 0.85)
        except Exception:
            return 3400  # Fallback
    
    def _is_streaming_endpoint(self, request: Request) -> bool:
        """Check if this is a streaming endpoint that needs continuation handling."""
        streaming_paths = ['/api/chat', '/ziya/stream', '/ziya/stream_log']
        return any(request.url.path.startswith(path) for path in streaming_paths)
    
    async def _wrap_streaming_response(
        self, 
        original_generator: AsyncGenerator, 
        request: Request
    ) -> AsyncGenerator[str, None]:
        """
        Wrap the streaming response to detect context overflow.
        
        This monitors the outgoing stream and triggers continuation
        when approaching limits.
        """
        accumulated_response = ""
        chunk_count = 0
        conversation_id = None
        
        try:
            async for chunk in original_generator:
                chunk_count += 1
                
                # Extract conversation ID from early chunks
                if not conversation_id and chunk_count < 5:
                    conversation_id = self._extract_conversation_id(chunk)
                
                # Track response content
                content = self._extract_content_from_chunk(chunk)
                if content:
                    accumulated_response += content
                
                # Check if we need continuation based on model's token limits
                current_threshold = self._get_response_threshold()
                
                # Rough token estimation
                estimated_tokens = len(accumulated_response) // 4
                
                if (estimated_tokens > current_threshold and 
                    not self._has_continuation_marker(chunk)):
                    
                    logger.info(f"🔄 MIDDLEWARE: Detected overflow, initiating continuation")
                    
                    # Find continuation point
                    continuation_point = self._find_continuation_point(accumulated_response)
                    
                    if continuation_point:
                        # Send completed part
                        completed_part = accumulated_response[:continuation_point]
                        remaining_part = accumulated_response[continuation_point:]
                        
                        # Yield the completed part
                        completion_chunk = self._create_content_chunk(completed_part)
                        yield completion_chunk
                        
                        # Signal continuation
                        continuation_signal = self._create_continuation_signal(
                            conversation_id, remaining_part
                        )
                        yield continuation_signal
                        
                        # Start new continuation stream
                        async for continuation_chunk in self._start_continuation(
                            conversation_id, remaining_part, request
                        ):
                            yield continuation_chunk
                        
                        return
                
                # Yield original chunk
                yield chunk
                
        except Exception as e:
            logger.error(f"Error in stream wrapper: {e}")
            # Yield error and end stream gracefully
            error_chunk = json.dumps({
                "ops": [{"op": "add", "path": "/error", "value": f"Stream error: {str(e)}"}]
            })
            yield f"data: {error_chunk}\n\n"
    
    def _extract_conversation_id(self, chunk: str) -> Optional[str]:
        """Extract conversation ID from chunk if present."""
        try:
            if "data:" in chunk:
                data_part = chunk.split("data:", 1)[1].strip()
                if data_part.startswith("{"):
                    data = json.loads(data_part)
                    return data.get("conversation_id")
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
        return None
    
    def _extract_content_from_chunk(self, chunk: str) -> str:
        """Extract actual content from SSE chunk."""
        try:
            if "data:" in chunk:
                data_part = chunk.split("data:", 1)[1].strip()
                if data_part.startswith("{"):
                    data = json.loads(data_part)
                    ops = data.get("ops", [])
                    content = ""
                    for op in ops:
                        if op.get("path") == "/streamed_output_str/-":
                            content += op.get("value", "")
                    return content
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass
        return ""
    
    def _has_continuation_marker(self, chunk: str) -> bool:
        """Check if chunk already has continuation marker."""
        return "continuation_started" in chunk
    def _find_continuation_point(self, text: str) -> Optional[int]:
        """Find appropriate continuation point in text."""
        import re
        
        # Helper to check if we're inside a code block at a given position
        def is_inside_code_block(text: str, position: int) -> tuple[bool, Optional[str]]:
            """
            Returns (is_inside, fence_language) where fence_language is the 
            language tag if we're inside a code block, None otherwise.
            
            This properly handles code fences by only counting those that are:
            1. At start of a line (possibly after whitespace)
            2. Not inside other code blocks
            """
            lines_before = text[:position].split('\n')
            
            code_block_stack = []  # Stack to track nested blocks (though rare)
            
            for line in lines_before:
                stripped = line.lstrip()
                
                # Check for code fence (``` or ~~~)
                fence_match = re.match(r'^(`{3,}|~{3,})(\w*)', stripped)
                if fence_match:
                    fence_chars = fence_match.group(1)
                    language = fence_match.group(2) or None
                    
                    # Check if this closes an existing block
                    if code_block_stack and code_block_stack[-1]['fence'] == fence_chars[0]:
                        code_block_stack.pop()
                    else:
                        # Open a new block
                        code_block_stack.append({
                            'fence': fence_chars[0],  # '`' or '~'
                            'language': language
                        })
            
            if code_block_stack:
                return (True, code_block_stack[-1]['language'])
            return (False, None)
        
        # Helper to find safe paragraph breaks (not inside code blocks)
        def find_safe_paragraph_breaks(text: str) -> list[int]:
            """Find paragraph breaks that are outside code blocks."""
            breaks = [m.end() for m in re.finditer(r'\n\n+', text)]
            safe_breaks = []
            
            for break_point in breaks:
                inside_block, _ = is_inside_code_block(text, break_point)
                if not inside_block:
                    safe_breaks.append(break_point)
            
            return safe_breaks

        # Look for paragraph breaks
        paragraph_breaks = find_safe_paragraph_breaks(text)
        if paragraph_breaks:
            for break_point in reversed(paragraph_breaks):
                if break_point < len(text) * 0.8:
                    logger.debug(f"🔄 CONTINUATION: Found safe paragraph break at position {break_point}")
                    return break_point

        # Look for sentence endings
        sentence_endings = [m.end() for m in re.finditer(r'[.!?]\s+', text)]
        if sentence_endings:
            for break_point in reversed(sentence_endings):
                # Check if this sentence ending is safe (not inside code block)
                inside_block, _ = is_inside_code_block(text, break_point)
                if inside_block:
                    continue
                    
                if break_point < len(text) * 0.8:
                    logger.debug(f"🔄 CONTINUATION: Found safe sentence ending at position {break_point}")
                    return break_point
        
        # If we couldn't find a safe break point, log warning
        logger.warning(f"🔄 CONTINUATION: No safe continuation point found in {len(text)} chars, will split at paragraph break regardless")
        
        # Fallback: use the last paragraph break even if inside code block
        # But include metadata about the code block state
        all_breaks = [m.end() for m in re.finditer(r'\n\n+', text)]
        if all_breaks:
            for break_point in reversed(all_breaks):
                if break_point < len(text) * 0.8:
                    return break_point

        return None
        
        return None
    
    def _create_content_chunk(self, content: str) -> str:
        """Create SSE chunk for content."""
        ops = [{"op": "add", "path": "/streamed_output_str/-", "value": content}]
        return f"data: {json.dumps({'ops': ops})}\n\n"
    
    def _create_continuation_signal(self, conversation_id: str, remaining: str) -> str:
        """Create signal that continuation is starting."""
        signal = {
            "continuation_started": True,
            "conversation_id": conversation_id,
            "remaining_preview": remaining[:100] if remaining else ""
        }
        return f"data: {json.dumps(signal)}\n\n"
    
    async def _start_continuation(
        self, 
        conversation_id: str, 
        remaining_content: str, 
        original_request: Request
    ) -> AsyncGenerator[str, None]:
        """Start a new continuation stream."""
        try:
            # Create continuation request
            continuation_prompt = f"Continue from: {remaining_content[:200]}..."
            
            # This would trigger a new request to the same endpoint
            # with modified context to continue seamlessly
            
            # For now, just yield the remaining content
            ops = [{"op": "add", "path": "/streamed_output_str/-", "value": remaining_content}]
            yield f"data: {json.dumps({'ops': ops})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except Exception as e:
            logger.error(f"Error in continuation: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
