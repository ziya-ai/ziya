"""
Middleware for handling streaming responses and errors.
"""

import os
import json
import re
from typing import AsyncIterator, Any
from app.agents.wrappers.nova_formatter import NovaFormatter
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
from starlette.responses import StreamingResponse, Response
from starlette.types import ASGIApp
from langchain_core.outputs import ChatGeneration
from langchain_core.messages import AIMessage
from langchain_core.messages import AIMessageChunk
from langchain_core.tracers.log_stream import RunLogPatch

from app.utils.logging_utils import logger

# Import Google AI error for proper handling
try:
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
except ImportError:
    ChatGoogleGenerativeAIError = None

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
        if is_streaming and ("/ziya/stream" in request.url.path or "/api/chat" in request.url.path):
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
        content_buffer = ""  # Initialize buffer
        accumulated_content = ""
        accumulated_chunks = []  # Track all chunks for better preservation

        # Code block state tracking for frontend
        frontend_code_block_state = {
            'in_block': False,
            'block_type': None
        }
        
        # Limits for preserved content to prevent context bloat
        MAX_PRESERVED_TOOLS = 10
        MAX_TOOL_OUTPUT_LENGTH = 5000
        successful_tool_outputs = []  # Track successful tool executions
        tool_sequence_count = 0
        partial_response_preserved = False
        
        try:
            async for chunk in original_iterator:
                # Check if this is a continuation boundary - pass through immediately without buffering
                chunk_str = str(chunk) if not isinstance(chunk, str) else chunk
                
                # Parse to check for continuation_boundary flag
                try:
                    if 'data: {' in chunk_str:
                        data_part = chunk_str.split('data: ', 1)[1].split('\n\n', 1)[0]
                        chunk_data = json.loads(data_part)
                        
                        # If this is a continuation boundary, yield immediately as atomic unit
                        if chunk_data.get('continuation_boundary'):
                            logger.info("🔄 MIDDLEWARE: Detected continuation boundary, passing through atomically")
                            yield chunk_str
                            continue
                except (json.JSONDecodeError, KeyError, AttributeError):
                    pass  # Not JSON or doesn't have the flag, continue normal processing
                
                # Log chunk info for debugging
                logger.info("=== AGENT astream received chunk ===")
                logger.info(f"Chunk type: {type(chunk)}")
                # Log thinking mode status
                thinking_mode_enabled = os.environ.get("ZIYA_THINKING_MODE") == "1"
                logger.debug(f"Thinking mode enabled: {thinking_mode_enabled}")
                
                chunk_content = ""
                
                # CRITICAL: Check if chunk is already a dict with error information
                # This must happen BEFORE any other processing to catch authentication errors
                if isinstance(chunk, dict):
                    # If this is an error dict, send it immediately as SSE
                    if 'error' in chunk or 'type' in chunk:
                        logger.info(f"🔐 MIDDLEWARE: Detected error dict, sending as SSE: {chunk.get('error', chunk.get('type'))}")
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                        
                        # If this is a terminal error, send DONE marker
                        if chunk.get('type') == 'error' or chunk.get('error') in ['authentication_error', 'model_error']:
                            logger.info("🔐 MIDDLEWARE: Sending DONE marker after error")
                            yield "data: [DONE]\n\n"
                            return
                        
                        continue
                
                # Process the chunk
                try:
                    # Handle ChatGoogleGenerativeAIError specifically
                    if ChatGoogleGenerativeAIError and isinstance(chunk, ChatGoogleGenerativeAIError):
                        logger.info("Processing ChatGoogleGenerativeAIError in streaming middleware")
                        error_message = str(chunk)
                        
                        # Check for context size error
                        if "exceeds the maximum number of tokens" in error_message:
                            error_data = {
                                "error": "context_size_error",
                                "detail": "The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.",
                                "status_code": 413
                            }
                        else:
                            error_data = {
                                "error": "model_error", 
                                "detail": error_message,
                                "status_code": 500
                            }
                        
                        yield f"data: {json.dumps(error_data)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    
                    # Handle AIMessageChunk objects
                    if isinstance(chunk, AIMessageChunk):
                        logger.info("Processing AIMessageChunk")
                    
                    # Get the raw content, preserving structure
                    raw_content = None
                    if hasattr(chunk, 'content'):
                        raw_content = chunk.content
                    else:
                        raw_content = chunk  # Assume chunk itself is the content
                    
                    # Check if this might be thinking mode content (typically more structured)
                    chunk_content = raw_content
                    is_structured = isinstance(raw_content, dict) and ('thinking' in raw_content or 'reasoning' in raw_content)
                    logger.debug(f"Content appears to be structured thinking: {is_structured}")
                    
                        # Let stream_chunks handle structured error detection within content
                    if raw_content: # Avoid sending empty data chunks
                            # Properly handle different content types
                        # Always accumulate content for preservation
                        if isinstance(raw_content, str):
                            accumulated_content += raw_content
                            accumulated_chunks.append(raw_content)
                            
                            # Track successful tool executions
                            if self._looks_like_tool_output(raw_content):
                                tool_sequence_count += 1
                                if not self._contains_error_indicators(raw_content):
                                    # Limit size of individual tool outputs
                                    tool_output = raw_content
                                    if len(tool_output) > MAX_TOOL_OUTPUT_LENGTH:
                                        tool_output = tool_output[:MAX_TOOL_OUTPUT_LENGTH] + f"\n... [Tool output truncated - {len(raw_content)} total chars]"
                                    
                                    successful_tool_outputs.append({
                                        "sequence": tool_sequence_count,
                                        "content": tool_output
                                    })
                                    
                                    # Limit total number of preserved tool outputs
                                    if len(successful_tool_outputs) > MAX_PRESERVED_TOOLS:
                                        successful_tool_outputs = successful_tool_outputs[-MAX_PRESERVED_TOOLS:]
                        if isinstance(raw_content, dict):
                            # For structured content like thinking mode, preserve the structure
                            logger.debug(f"Preserving structured content: {list(raw_content.keys())}")
                            yield f"data: {json.dumps(raw_content, ensure_ascii=False)}\n\n"
                            chunk_content = json.dumps(raw_content, ensure_ascii=False)
                        else:
                            # For simple string content
                            content = str(raw_content)
                            
                            # Immediately pass through tool_start messages without buffering
                            if '"type": "tool_start"' in content:
                                # First flush any buffered content
                                if content_buffer:
                                    yield f"data: {json.dumps({'content': content_buffer})}\n\n"
                                    content_buffer = ""
                                # Then send the tool_start message
                                yield content
                                continue
                            
                            # Buffer content to check for tool calls
                            content_buffer += content
                            
                            # Check if we have a complete tool call or if we should flush the buffer
                            if self._should_flush_buffer(content_buffer):
                                # If this contains a complete tool call, execute it and send the result
                                if self._contains_complete_tool_call(content_buffer):
                                    # First, send the complete tool call to the frontend as JSON
                                    yield f"data: {json.dumps({'tool_call': content_buffer})}\n\n"
                                    
                                    try:
                                        # Execute the tool call
                                        logger.info(f"Executing tool call in streaming middleware: {content_buffer[:100]}...")
                                        from app.mcp.consolidated import execute_mcp_tools_with_status
                                        tool_result = await execute_mcp_tools_with_status(content_buffer)
                                        logger.info(f"Tool execution result: {tool_result[:100]}...")
                                        
                                        # Send the tool result to the frontend
                                        yield f"data: {json.dumps({'tool_result': tool_result})}\n\n"
                                    except Exception as tool_error:
                                        logger.error(f"Error executing tool call: {tool_error}")
                                        # Send error message
                                        error_msg = f"\n\n```tool:error\n❌ **Tool Error:** {str(tool_error)}\n```\n\n"
                                        yield f"data: {json.dumps({'tool_error': error_msg})}\n\n"
                                    
                                    # Clear buffer
                                    content_buffer = ""
                                else:
                                    # Send buffered content as JSON, not raw content
                                    yield f"data: {json.dumps({'content': content_buffer})}\n\n"
                                    content_buffer = ""
                            elif self._contains_partial(content_buffer):
                                # Still accumulate partial content
                                accumulated_content += content
                                # Hold the content in buffer, don't send yet
                                continue
                            else:
                                # Safe to send immediately as raw content
                                yield f"data: {content}\n\n"
                                content_buffer = ""
                        
                        if not content_buffer:  # Only continue if we're not buffering
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
                            # DEBUGGING: Check for large chunks that might get dropped
                            chunk_size = len(chunk)
                            if chunk_size > 10000:
                                logger.warning(f"🔍 LARGE_CHUNK_DETECTED: {chunk_size} chars - monitoring for truncation")
                            
                            # Check if this is a tool result chunk
                            if "tool_execution" in chunk or "tool_display" in chunk or "tool_result" in chunk:
                                logger.debug(f"🔍 TOOL_RESULT_CHUNK: size={chunk_size}, content preview: {chunk[:100]}...")
                            
                            # Check if it might be JSON
                            try:
                                # Try to parse as JSON to validate
                                json_obj = json.loads(chunk)
                                yield f"data: {json.dumps(json_obj)}\n\n"
                                
                                # DEBUGGING: Track large JSON objects
                                if len(json.dumps(json_obj)) > 5000:
                                    logger.warning(f"🔍 MIDDLEWARE_LARGE_JSON: {len(json.dumps(json_obj))} chars, type={json_obj.get('type')}")
                                if json_obj.get('type') in ['tool_execution', 'tool_display']:
                                    result_size = len(json_obj.get('result', ''))
                                    logger.warning(f"🔍 MIDDLEWARE_TOOL_RESULT: tool={json_obj.get('tool_name')}, result_size={result_size}")
                                    if result_size == 0:
                                        logger.error(f"🔍 MIDDLEWARE_EMPTY_RESULT: Tool result is empty after JSON processing!")
                            except json.JSONDecodeError:
                                # If it's not valid JSON, just pass it as a string
                                yield f"data: {chunk}\n\n"
                        
                    # If we get here, try to extract content from the chunk
                    if hasattr(chunk, 'content'):
                        logger.info("Extracting content from chunk")
                        content = chunk.content
                        if callable(content):
                            content = content()
                        if content:
                            if isinstance(content, dict):
                                accumulated_content += json.dumps(content)
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
                            # Find where the JSON ends - look for [DONE] marker
                            if '[DONE]' in chunk_str:
                                json_part = chunk_str.split('[DONE]')[0].strip()
                            elif '}data:' in chunk_str:
                                json_part = chunk_str.split('}data:')[0] + '}'
                            elif chunk_str.endswith('}[DONE]'):
                                # Handle the specific case where [DONE] is appended to JSON
                                json_part = chunk_str[:-6]  # Remove '[DONE]' suffix
                            elif chunk_str.endswith('}'):
                                # Clean JSON without any suffix
                                json_part = chunk_str
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
                            logger.info(f"Extracted JSON part: {json_part}")
                            error_data = json.loads(json_part)
                            yield f"data: {json.dumps(error_data)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse error JSON: {json_part if 'json_part' in locals() else chunk_str}")
                    
                    # Fallback: treat as regular chunk processing error
                    error_msg = {"error": "chunk_processing_error", "detail": str(chunk_error)}
                    yield f"data: {json.dumps(error_msg)}\n\n"
                    
                    # Preserve accumulated content before error
                    if accumulated_content and not partial_response_preserved:
                        logger.info(f"Preserving {len(accumulated_content)} characters of partial response before chunk error")
                        
                        # Create a comprehensive preservation message
                        preservation_content = {
                            "partial_content": accumulated_content,
                            "chunks_processed": len(accumulated_chunks)
                        }
                        
                        # Send warning about partial response
                        warning_msg = {
                            "warning": "partial_response_preserved",
                            "detail": f"Server encountered an error after generating {len(accumulated_content)} characters. The partial response has been preserved.",
                            "partial_content": accumulated_content,
                            "successful_tool_outputs": successful_tool_outputs,
                            "execution_summary": {
                                "total_tool_sequences": tool_sequence_count,
                                "successful_sequences": len(successful_tool_outputs),
                                "has_successful_tools": len(successful_tool_outputs) > 0
                            }
                        }
                        yield f"data: {json.dumps(warning_msg)}\n\n"
                        partial_response_preserved = True
                        
                        # Also send as a custom event for the frontend to handle
                        event_data = {
                            "type": "preservedContent",
                            "data": warning_msg
                }
                yield f"event: preservedContent\n"
                yield f"data: {json.dumps(event_data)}\n\n"
            
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
            
        except Exception as e:
            logger.error(f"Stream processing error: {str(e)}")
            # Try to send the [DONE] marker
            try:
                yield "data: [DONE]\n\n"
            except Exception as done_error:
                logger.error(f"Error sending DONE marker: {str(done_error)}")
    
    def _contains_partial(self, content: str) -> bool:
        """Check if content contains the start of a tool call but not the end."""
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        # Check for configurable sentinels
        config_partial = TOOL_SENTINEL_OPEN in content and TOOL_SENTINEL_CLOSE not in content
        
        # Check for hardcoded sentinels
        hardcoded_partial = "<TOOL_SENTINEL>" in content and "</TOOL_SENTINEL>" not in content
        
        # Check for generic XML-style tags with unbalanced opening/closing tags
        # This handles both <invoke> and custom tool tags
        import re
        xml_tags = re.findall(r'<([a-zA-Z_][a-zA-Z0-9_]*)[^>]*>', content)
        for tag in xml_tags:
            if f"<{tag}" in content and f"</{tag}>" not in content:
                return True
        
        # Check for specific tool tags we know about
        known_tools = ["get_current_time", "run_shell_command"]
        for tool in known_tools:
            if f"<{tool}" in content and f"</{tool}>" not in content:
                return True
        
        return config_partial or hardcoded_partial
    
    def _contains_complete_tool_call(self, content: str) -> bool:
        """Check if content contains a complete tool call."""
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        # For TOOL_SENTINEL format, check if it has both name and arguments tags
        if TOOL_SENTINEL_OPEN in content and TOOL_SENTINEL_CLOSE in content:
            has_n_name = "<n>" in content and "</n>" in content
            has_name_name = "<name>" in content and "</name>" in content
            has_args = "<arguments>" in content and "</arguments>" in content
            return (has_n_name or has_name_name) and has_args
        
        if "<TOOL_SENTINEL>" in content and "</TOOL_SENTINEL>" in content:
            has_n_name = "<n>" in content and "</n>" in content
            has_name_name = "<name>" in content and "</name>" in content
            has_args = "<arguments>" in content and "</arguments>" in content
            return (has_n_name or has_name_name) and has_args
        
        # Check for specific tool formats
        if "<get_current_time>" in content and "</get_current_time>" in content:
            return True
            
        if "<run_shell_command>" in content and "</run_shell_command>" in content:
            return "<command>" in content and "</command>" in content
            
        if "<invoke name=" in content and "</invoke>" in content:
            return True
        
        # Check for generic XML-style tags with balanced opening/closing tags
        import re
        xml_pattern = r'<([a-zA-Z_][a-zA-Z0-9_]*)[^>]*>(.*?)</\1>'
        xml_match = re.search(xml_pattern, content, re.DOTALL)
        
        # Special case for <TOOL_SENTINEL><n>tool_name</n>
        if "<TOOL_SENTINEL>" in content and "<n>" in content and "</n>" in content and "</TOOL_SENTINEL>" not in content:
            return False
            
        return bool(xml_match)
    
    async def _execute_tool_call(self, content: str) -> str:
        """Execute a tool call in the content and return the result."""
        try:
            # Import the MCP tool execution function
            from app.mcp.consolidated import execute_mcp_tools_with_status
            
            # Execute the tool call
            logger.info(f"Executing tool call in streaming middleware: {content[:100]}...")
            result = await execute_mcp_tools_with_status(content)
            logger.info(f"Tool execution result: {result[:100]}...")
            
            return result
        except Exception as e:
            logger.error(f"Error executing tool call in streaming middleware: {e}")
            # Return error message
            return f"\n\n```tool:error\n❌ **Tool Error:** {str(e)}\n```\n\n"
    
    def _should_flush_buffer(self, buffer: str) -> bool:
        """Determine if we should flush the buffer."""
        # Always flush if we have a complete tool call
        if self._contains_complete_tool_call(buffer):
            return True
        
        # Always flush if buffer is getting too large (safety measure)
        if len(buffer) > 500:  # Reduced from 1000 to prevent long delays
            return True
        
        # NEW LOGIC: Only hold content if we're clearly in the middle of a tool call
        # Don't hold content just because it "might" lead to a tool call
        if self._contains_partial(buffer):
            # We have partial tool call content - hold it
            return False
        
        # For everything else, flush immediately to prevent delays
        # This includes regular text that doesn't look like tool content
        if buffer and len(buffer.strip()) > 0:
            return True
        
        return False
    
    def _might_be_tool_start(self, content: str) -> bool:
        """Check if content might be the start of a tool call."""
        from app.config.models_config import TOOL_SENTINEL_OPEN
        
        # Check for configurable sentinel
        sentinel_start = TOOL_SENTINEL_OPEN[:min(len(content), len(TOOL_SENTINEL_OPEN))]
        config_match = content.endswith(sentinel_start) or TOOL_SENTINEL_OPEN.startswith(content.strip())
        
        # Check for hardcoded sentinel
        hardcoded_sentinel = "<TOOL_SENTINEL>"
        hardcoded_start = hardcoded_sentinel[:min(len(content), len(hardcoded_sentinel))]
        hardcoded_match = content.endswith(hardcoded_start) or hardcoded_sentinel.startswith(content.strip())
        
        # Check for tool name tags - both <n> and <name> formats
        name_tag_patterns = ["<n>", "<name>"]
        name_tag_match = any(pattern in content for pattern in name_tag_patterns)
        
        # Check for common tool tag prefixes
        common_prefixes = ["<get", "<run", "<inv", "<TOOL", "<name>", "<n>"]
        prefix_match = any(content.endswith(prefix) or content.strip().startswith(prefix) for prefix in common_prefixes)
        
        # Check for specific tool names we know about
        known_tools = ["get_current_time", "run_shell_command", "mcp_run_shell_command", "mcp_get_current_time"]
        for tool in known_tools:
            tool_start = f"<{tool}"[:min(len(content), len(tool) + 1)]
            if content.endswith(tool_start) or f"<{tool}".startswith(content.strip()):
                return True
        
        # Check for generic XML-style opening tag
        xml_match = "<" in content and content.rstrip().endswith(">")
        
        return config_match or hardcoded_match or prefix_match or name_tag_match or xml_match
    
    def _looks_like_tool_output(self, content: str) -> bool:
        """Check if content looks like tool output."""
        tool_indicators = [
            "$ ",  # Shell command output
            "MCP Tool",
            "Tool:",
            "```",  # Code blocks often contain tool results
            "Exit code:",
            "SECURITY BLOCK",
            "Tool execution"
        ]
        return any(indicator in content for indicator in tool_indicators)
    
    def _contains_error_indicators(self, content: str) -> bool:
        """Check if content contains error indicators."""
        error_indicators = ["error", "timeout", "failed", "exception", "❌", "🚫", "⏱️"]
        return any(indicator.lower() in content.lower() for indicator in error_indicators)
