"""
MCP integration with the main server.

This module provides functions for integrating MCP capabilities with the main server,
including detecting and executing tool calls in model responses.
"""

import asyncio
from typing import Optional, Dict, Any, Set, Tuple

from app.utils.logging_utils import logger
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
from app.mcp.utils import clean_sentinels
from app.mcp.consolidated import execute_mcp_tools_with_status

async def detect_and_execute_mcp_tools(full_response: str, processed_calls: Optional[Set[str]] = None) -> str:
    """
    Detect and execute MCP tool calls in the response.
    
    This function handles tool calls robustly and prevents sentinel leakage.
    It uses the consolidated MCP execution system for better security and reliability.
    
    Args:
        full_response: The full response text from the model
        processed_calls: Optional set of already processed tool calls
        
    Returns:
        The response with tool calls replaced by their results
    """
    logger.info(f"🔧 MCP: detect_and_execute_mcp_tools called with response length: {len(full_response)}")
    
    # Always clean the response first to handle any stray sentinels
    cleaned_response = clean_sentinels(full_response)
    
    # Check if response contains tool calls
    if "<TOOL_SENTINEL>" not in full_response and TOOL_SENTINEL_OPEN not in full_response:
        # No tool calls, just return the cleaned response
        if cleaned_response != full_response:
            logger.info("🔧 MCP: Cleaned stray sentinels from response")
        return cleaned_response
    
    # Execute tools using the consolidated system
    try:
        result = await execute_mcp_tools_with_status(full_response)
        logger.info(f"🔧 MCP: Tool execution completed, result length: {len(result)}")
        
        # Clean the result again to ensure no sentinels remain
        final_result = clean_sentinels(result)
        if final_result != result:
            logger.info("🔧 MCP: Cleaned additional sentinels from result")
            
        return final_result
    except Exception as e:
        logger.error(f"🔧 MCP: Error executing tools: {e}")
        # Clean sentinels and return original response
        return clean_sentinels(full_response)
async def handle_streaming_tool_execution(chunk: str, processor: Optional[Any] = None) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Handle tool execution during streaming with improved robustness.
    
    This function processes a chunk of streaming response, detects and executes
    tool calls, and returns the cleaned chunk and tool result.
    
    Args:
        chunk: A chunk of text from the streaming response
        processor: Optional StreamingToolProcessor instance for state management
        
    Returns:
        Tuple of (cleaned chunk, tool result if any)
    """
    from app.mcp.utils import StreamingToolProcessor, clean_sentinels
    
    # Create or use the provided processor
    if processor is None:
        processor = StreamingToolProcessor()
    
    # First, check if the chunk contains any sentinel markers
    # If not, we can skip the expensive processing
    if ("<TOOL_SENTINEL>" not in chunk and 
        "</TOOL_SENTINEL>" not in chunk and
        TOOL_SENTINEL_OPEN not in chunk and
        TOOL_SENTINEL_CLOSE not in chunk and
        "<name>" not in chunk and
        "<arguments>" not in chunk):
        # No tool markers, just clean any stray fragments and return
        return clean_sentinels(chunk), None
    
    # Process the chunk
    cleaned_chunk, tool_info = processor.process_chunk(chunk)
    
    # If a tool was detected, execute it
    if tool_info:
        try:
            # Construct a complete tool call for execution
            tool_name = tool_info['name']
            arguments = tool_info['arguments']
            
            # Format the tool call
            tool_call = f"<TOOL_SENTINEL><name>{tool_name}</name><arguments>{json.dumps(arguments)}</arguments></TOOL_SENTINEL>"
            
            # Execute the tool
            result = await execute_mcp_tools_with_status(tool_call)
            
            # Return the cleaned chunk and tool result
            return cleaned_chunk, {
                'name': tool_name,
                'arguments': arguments,
                'result': result
            }
        except Exception as e:
            logger.error(f"Error executing tool during streaming: {e}")
            # Return the cleaned chunk and error information
            return cleaned_chunk, {
                'name': tool_info.get('name', 'unknown'),
                'arguments': tool_info.get('arguments', {}),
                'error': str(e)
            }
    
    # No tool detected, just return the cleaned chunk
    return cleaned_chunk, None
__all__ = [
    'detect_and_execute_mcp_tools',
    'handle_streaming_tool_execution',
    'cleanup_secure_streaming'
]
