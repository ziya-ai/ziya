"""
MCP tools integration for Ziya's agent system.

This module provides LangChain-compatible tools that wrap MCP server capabilities,
allowing the agent to use MCP tools seamlessly.
"""

import re
import os
import json

import time
import asyncio
from typing import Dict, List, Any, Optional, Type
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
from langchain.callbacks.manager import CallbackManagerForToolRun, AsyncCallbackManagerForToolRun

from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger
from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE

# Global counter for tracking tool execution order and implementing progressive delays
_tool_execution_counter = 0
_tool_execution_lock = asyncio.Lock()
# Global timeout tracking for consecutive timeouts
_consecutive_timeouts = {}
_timeout_lock = asyncio.Lock()

# Base delay in seconds for progressive throttling
# Can be configured via environment variable
BASE_DELAY_SECONDS = int(os.environ.get("MCP_TOOL_DELAY_SECONDS", "5"))

# Maximum number of sequential MCP commands per request cycle
MAX_SEQUENTIAL_TOOLS = int(os.environ.get("MCP_MAX_SEQUENTIAL_TOOLS", "10"))

# Maximum size for tool output (in characters)
MAX_TOOL_OUTPUT_SIZE = int(os.environ.get("MCP_MAX_TOOL_OUTPUT_SIZE", "10000"))

async def _reset_counter_async():
    """Reset the tool execution counter asynchronously."""
    global _tool_execution_counter
    async with _tool_execution_lock:
        _tool_execution_counter = 0
        _consecutive_timeouts.clear()  # Reset timeout tracking on new request cycle
        logger.info("🔄 MCP Tool counter reset for new request cycle")

def parse_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """
    Parse tool calls from content, supporting multiple formats.
    
    Supports both:
    - {TOOL_SENTINEL_OPEN}<name>tool_name</name><arguments>...</arguments>{TOOL_SENTINEL_CLOSE}
    - {TOOL_SENTINEL_OPEN}<invoke name="tool_name"><parameter name="param">value</parameter></invoke>{TOOL_SENTINEL_CLOSE}
    
    Returns:
        Dict with tool_name and arguments, or None if no valid tool call found
    """
    # Log the content being parsed for debugging
    if TOOL_SENTINEL_OPEN in content:
        logger.debug(f"🔍 PARSE: Attempting to parse tool call from content: {content[:200]}...")

    # Format 1: <name> and <arguments>
    name_args_pattern = re.escape(TOOL_SENTINEL_OPEN) + r'\s*<name>([^<]+)</name>\s*<arguments>\s*(\{.*?\})\s*</arguments>\s*' + re.escape(TOOL_SENTINEL_CLOSE)

    match = re.search(name_args_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            import json
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed format 1 - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse arguments for tool {tool_name}: {match.group(2)}")
            return None

    # Format 2: <invoke> and <parameter>
    invoke_pattern = rf'{re.escape(TOOL_SENTINEL_OPEN)}\s*<invoke\s+name="([^"]+)">\s*(.*?)\s*</invoke>\s*{re.escape(TOOL_SENTINEL_CLOSE)}'
    match = re.search(invoke_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        params_content = match.group(2)
        
        # Parse parameters
        param_pattern = r'<parameter\s+name="([^"]+)">([^<]*)</parameter>'
        params = {}
        for param_match in re.finditer(param_pattern, params_content):
            param_name = param_match.group(1)
            param_value = param_match.group(2).strip()
            params[param_name] = param_value
        
        return {"tool_name": tool_name, "arguments": params}

    # Log if no tool call pattern was found
    if TOOL_SENTINEL_OPEN in content:
        logger.warning(f"Found {TOOL_SENTINEL_OPEN} tag but couldn't parse it. Content: {content[:200]}...")
    
    return None

async def _track_timeout(tool_name: str) -> bool:
    """
    Track consecutive timeouts for a tool.
    Returns True if this is the 3rd consecutive timeout and should be shown.
    """
    async with _timeout_lock:
        if tool_name not in _consecutive_timeouts:
            _consecutive_timeouts[tool_name] = 0
        
        _consecutive_timeouts[tool_name] += 1
        return _consecutive_timeouts[tool_name] >= 3

async def _reset_timeout_counter(tool_name: str):
    """Reset timeout counter for a tool after successful execution."""
    async with _timeout_lock:
        if tool_name in _consecutive_timeouts:
            _consecutive_timeouts[tool_name] = 0

class MCPToolInput(BaseModel):
    """Input schema for MCP tools."""
    arguments: Dict[str, Any] = Field(description="Arguments to pass to the MCP tool")


class MCPTool(BaseTool):
    """
    LangChain tool wrapper for MCP tools.
    
    This allows MCP tools to be used seamlessly within Ziya's agent system.
    """
    
    name: str
    description: str
    mcp_tool_name: str
    args_schema: Type[BaseModel] = MCPToolInput
    
    def _run(
        self,
        arguments: Dict[str, Any],
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Run the MCP tool synchronously."""
        logger.info(f"MCPTool._run called for {self.mcp_tool_name} with args: {arguments}")
        # Run the async version in a new event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self._arun(arguments, None))
    
    async def _arun(
        self,
        arguments: Dict[str, Any],
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        """Run the MCP tool asynchronously."""
        logger.info(f"MCPTool._arun called for {self.mcp_tool_name} with args: {arguments}")
        logger.info(f"🔍 MCPTool._arun: About to execute MCP tool {self.mcp_tool_name}")
        logger.info(f"🔍 MCPTool._arun: MCP manager initialized: {mcp_manager.is_initialized if 'mcp_manager' in globals() else 'No manager'}")
        
        # Implement progressive delay to prevent Bedrock throttling
        global _tool_execution_counter
        async with _tool_execution_lock:
            # Check if we've hit the sequential tool limit
            if _tool_execution_counter >= MAX_SEQUENTIAL_TOOLS:
                return f"⚠️ **Tool Execution Limit Reached**: Maximum of {MAX_SEQUENTIAL_TOOLS} sequential tools per request cycle. Tool '{self.mcp_tool_name}' was not executed to prevent system overload."
            
            current_execution_order = _tool_execution_counter
            _tool_execution_counter += 1
        
        # Calculate delay: first tool (order 0) = 0s, second = 5s, third = 10s, etc.
        delay_seconds = current_execution_order * BASE_DELAY_SECONDS
        
        if delay_seconds > 0:
            logger.info(f"🕐 MCP Tool throttling: Waiting {delay_seconds}s before executing {self.mcp_tool_name} (execution #{current_execution_order + 1}, base_delay={BASE_DELAY_SECONDS}s)")
            
            await asyncio.sleep(delay_seconds)
            logger.info(f"🕐 MCP Tool throttling: Delay complete, executing {self.mcp_tool_name}")
        else:
            logger.info(f"🕐 MCP Tool throttling: No delay for first tool execution ({self.mcp_tool_name})")
        
        # Track partial results for timeout/error recovery
        partial_content = ""
        execution_start_time = asyncio.get_event_loop().time()
        
        # Build status message that will be included in the result
        status_parts = []
        status_parts.append(f"🔧 **Executing Tool**: {self.mcp_tool_name}")
        if delay_seconds > 0:
            status_parts.append(f"⏳ **Throttling Delay**: Waited {delay_seconds} seconds to prevent rate limiting")
        
        try:
            mcp_manager = get_mcp_manager()
            
            # Validate that we have a real MCP manager
            if not mcp_manager or not mcp_manager.is_initialized:
                return f"❌ **MCP Error**: MCP manager not available for tool '{self.mcp_tool_name}'"
            
            # Execute with timeout handling and partial content preservation
            try:
                # Set a reasonable timeout for MCP calls (30 seconds)
                result = await asyncio.wait_for(
                    mcp_manager.call_tool(self.mcp_tool_name, arguments),
                    timeout=30.0
                )
                
                # Validate we got a real result, not a simulation
                if result is None:
                    return f"❌ **MCP Error**: Tool '{self.mcp_tool_name}' returned no result"
                
            except asyncio.TimeoutError:
                execution_time = asyncio.get_event_loop().time() - execution_start_time
                
                # Track consecutive timeouts
                should_show_timeout = await _track_timeout(self.mcp_tool_name)
                
                if not should_show_timeout:
                    logger.debug(f"MCP tool {self.mcp_tool_name} timed out (suppressed - count: {_consecutive_timeouts.get(self.mcp_tool_name, 0)}/3)")
                    return ""  # Return empty string to suppress timeout message
                
                # Return timeout error with context preservation
                timeout_data = {
                    "error": "mcp_timeout", 
                    "detail": f"MCP tool '{self.mcp_tool_name}' timed out after {execution_time:.1f} seconds",
                    "tool_name": self.mcp_tool_name,
                    "arguments": arguments,
                    "partial_content": partial_content,
                    "execution_time": execution_time
                }
                
                # Format as a user-friendly error message that preserves context
                timeout_result = f"⏱️ **MCP Tool Timeout**: {self.mcp_tool_name} timed out after {execution_time:.1f}s"
                if partial_content:
                    timeout_result += f"\n\n**Partial Result Before Timeout:**\n{partial_content}"
                timeout_result += f"\n\n**Arguments:** {arguments}"
                return timeout_result
            
            logger.info(f"🔍 MCPTool._arun: Got result from MCP manager: {result}")
            
            # Reset timeout counter on successful execution
            await _reset_timeout_counter(self.mcp_tool_name)
            
            # Log successful execution for throttling analysis
            logger.info(f"✅ MCP Tool execution completed: {self.mcp_tool_name} (execution #{current_execution_order + 1}, delay was {delay_seconds}s)")
            
            # Check if this is an error response from the MCP server
            if isinstance(result, dict) and result.get("error"):
                error_msg = result.get("message", "Unknown MCP error")
                error_code = result.get("code", -1)
                
                # Check for timeout-related errors from the MCP server itself
                is_timeout_error = (
                    "timeout" in error_msg.lower() or 
                    "timed out" in error_msg.lower() or
                    error_code == -32603  # Internal error code often used for timeouts
                )
                
                if is_timeout_error:
                    # Track consecutive timeouts for server-side timeouts too
                    should_show_timeout = await _track_timeout(self.mcp_tool_name)
                    
                    if not should_show_timeout:
                        logger.debug(f"MCP server timeout for {self.mcp_tool_name} (suppressed - count: {_consecutive_timeouts.get(self.mcp_tool_name, 0)}/3)")
                        return ""  # Return empty string to suppress timeout message
                    
                    execution_time = asyncio.get_event_loop().time() - execution_start_time
                    timeout_msg = f"⏱️ **MCP Server Timeout** (3+ consecutive): {error_msg}"
                    if partial_content:
                        timeout_msg += f"\n\n**Partial Result Before Timeout:**\n{partial_content}"
                    timeout_msg += f"\n\n**Execution Time:** {execution_time:.1f}s"
                    return timeout_msg
                
                # Format security errors prominently
                if "SECURITY BLOCK" in error_msg or "Command not allowed" in error_msg:
                    return f"🚫 **SECURITY BLOCK**: {error_msg}"
                else:
                    return f"❌ **MCP Error**: {error_msg}"
            
            logger.info(f"MCPTool._arun result for {self.mcp_tool_name}: {result}")
            if result is None:
                return f"Error: MCP tool '{self.mcp_tool_name}' not found or failed to execute"
            
            # Helper function to truncate large outputs
            def truncate_if_needed(text):
                if isinstance(text, str) and len(text) > MAX_TOOL_OUTPUT_SIZE:
                    truncated = text[:MAX_TOOL_OUTPUT_SIZE]
                    return f"{truncated}\n\n... [Output truncated - {len(text)} total characters, showing first {MAX_TOOL_OUTPUT_SIZE}]"
                return text
            
            # Format the result for the agent
            if isinstance(result, dict):
                if "content" in result:
                    # Prepend status information
                    status_header = "\n".join(status_parts) + "\n\n"
                    
                    content = result["content"]
                    if isinstance(content, list):
                        # Handle multiple content blocks
                        text_parts = []
                        for block in content[:1]: # only use the first content block to avoid duplicates
                            if isinstance(block, dict) and "text" in block:
                                # Track partial content as we process it
                                block_text = block["text"]
                                partial_content += block_text
                                text_parts.append(truncate_if_needed(block["text"]))
                            elif isinstance(block, str):
                                partial_content += block
                                text_parts.append(truncate_if_needed(block))
                        return status_header + truncate_if_needed("\n".join(text_parts))
                    elif isinstance(content, str):
                        partial_content += content
                        return status_header + truncate_if_needed(content)
                    else:
                        content_str = str(content)
                        partial_content += content_str
                        return status_header + truncate_if_needed(str(content))
                else:
                    result_str = str(result)
                    partial_content += result_str
                    return "\n".join(status_parts) + "\n\n" + truncate_if_needed(str(result))
            else:
                result_str = str(result)
                partial_content += result_str
                return "\n".join(status_parts) + "\n\n" + truncate_if_needed(str(result))
                
        except Exception as e:
            execution_time = asyncio.get_event_loop().time() - execution_start_time
            logger.error(f"Error running MCP tool {self.mcp_tool_name}: {str(e)}")
            
            # Log failed execution for throttling analysis
            logger.error(f"❌ MCP Tool execution failed: {self.mcp_tool_name} (execution #{current_execution_order + 1}, delay was {delay_seconds}s, execution_time={execution_time:.1f}s)")
            
            # Enhanced error message with context preservation
            error_msg = f"❌ **MCP Tool Error**: {str(e)}"
            if partial_content:
                error_msg += f"\n\n**Partial Result Before Error:**\n{partial_content}"
            error_msg += f"\n\n**Tool:** {self.mcp_tool_name}"
            error_msg += f"\n**Arguments:** {arguments}"
            error_msg += f"\n**Execution Time:** {execution_time:.1f}s"
            
            return error_msg


class MCPResourceTool(BaseTool):
    """
    LangChain tool for accessing MCP resources.
    """
    
    name: str = "mcp_get_resource"
    description: str = "Get content from an MCP resource by URI"
    
    class MCPResourceInput(BaseModel):
        uri: str = Field(description="URI of the resource to retrieve")
        server_name: Optional[str] = Field(None, description="Specific MCP server to query")
    
    args_schema: Type[BaseModel] = MCPResourceInput
    
    def _run(
        self,
        uri: str,
        server_name: Optional[str] = None,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Get MCP resource content synchronously."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self._arun(uri, server_name, None))
    
    async def _arun(
        self,
        uri: str,
        server_name: Optional[str] = None,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> str:
        """Get MCP resource content asynchronously."""
        try:
            mcp_manager = get_mcp_manager()
            content = await mcp_manager.get_resource_content(uri, server_name)
            
            if content is None:
                return f"Error: Resource '{uri}' not found"
            
            return content
            
        except Exception as e:
            logger.error(f"Error getting MCP resource {uri}: {str(e)}")
            return f"Error getting resource: {str(e)}"


def create_mcp_tools() -> List[BaseTool]:
    """
    Create LangChain tools from available MCP tools.
    
    Returns:
        List of LangChain-compatible tools
    """
    tools = []
    
    try:
        mcp_manager = get_mcp_manager()
        
        # Add resource access tool
        tools.append(MCPResourceTool())
        
        # Add tools from all connected MCP servers
        logger.info(f"Creating MCP tools from {len(mcp_manager.get_all_tools())} available tools")
        for mcp_tool in mcp_manager.get_all_tools():
            # Ensure tool name has mcp_ prefix for consistency
            tool_name = f"mcp_{mcp_tool.name}" if not mcp_tool.name.startswith("mcp_") else mcp_tool.name
            logger.info(f"Creating tool: {tool_name} from MCP tool: {mcp_tool.name}")
            tool = MCPTool(
                name=tool_name,
                description=mcp_tool.description,
                mcp_tool_name=mcp_tool.name  # Keep original name for actual MCP calls
            )
            tools.append(tool)
            
        logger.info(f"Created {len(tools)} MCP tools for agent")
        
    except Exception as e:
        logger.error(f"Error creating MCP tools: {str(e)}")
        logger.error(f"MCP manager initialized: {mcp_manager.is_initialized if 'mcp_manager' in locals() else 'No manager'}")
    
    return tools
