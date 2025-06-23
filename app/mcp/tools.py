"""
MCP tools integration for Ziya's agent system.

This module provides LangChain-compatible tools that wrap MCP server capabilities,
allowing the agent to use MCP tools seamlessly.
"""

import re
import json

import asyncio
from typing import Dict, List, Any, Optional, Type
from pydantic import BaseModel, Field
from langchain.tools import BaseTool
from langchain.callbacks.manager import CallbackManagerForToolRun, AsyncCallbackManagerForToolRun

from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger
from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE

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
        
        # Track partial results for timeout/error recovery
        partial_content = ""
        execution_start_time = asyncio.get_event_loop().time()
        
        try:
            mcp_manager = get_mcp_manager()
            
            # Execute with timeout handling and partial content preservation
            try:
                # Set a reasonable timeout for MCP calls (30 seconds)
                result = await asyncio.wait_for(
                    mcp_manager.call_tool(self.mcp_tool_name, arguments),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                execution_time = asyncio.get_event_loop().time() - execution_start_time
                logger.warning(f"MCP tool {self.mcp_tool_name} timed out after {execution_time:.1f} seconds")
                
                # Return timeout error with context preservation
                timeout_result = {
                    "error": "mcp_timeout", 
                    "detail": f"MCP tool '{self.mcp_tool_name}' timed out after {execution_time:.1f} seconds",
                    "tool_name": self.mcp_tool_name,
                    "arguments": arguments,
                    "partial_content": partial_content,
                    "execution_time": execution_time
                }
                
                # Format as a user-friendly error message that preserves context
                error_msg = f"⏱️ **MCP Tool Timeout**: {self.mcp_tool_name} timed out after {execution_time:.1f}s"
                if partial_content:
                    error_msg += f"\n\n**Partial Result Before Timeout:**\n{partial_content}"
                error_msg += f"\n\n**Arguments:** {arguments}"
                return error_msg
            
            logger.info(f"🔍 MCPTool._arun: Got result from MCP manager: {result}")
            
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
                    execution_time = asyncio.get_event_loop().time() - execution_start_time
                    timeout_msg = f"⏱️ **MCP Server Timeout**: {error_msg}"
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
            
            # Format the result for the agent
            if isinstance(result, dict):
                if "content" in result:
                    content = result["content"]
                    if isinstance(content, list):
                        # Handle multiple content blocks
                        text_parts = []
                        for block in content[:1]: # only use the first content block to avoid duplicates
                            if isinstance(block, dict) and "text" in block:
                                # Track partial content as we process it
                                block_text = block["text"]
                                partial_content += block_text
                                text_parts.append(block["text"])
                            elif isinstance(block, str):
                                partial_content += block
                                text_parts.append(block)
                        return "\n".join(text_parts)
                    elif isinstance(content, str):
                        partial_content += content
                        return content
                    else:
                        content_str = str(content)
                        partial_content += content_str
                        return str(content)
                else:
                    result_str = str(result)
                    partial_content += result_str
                    return str(result)
            else:
                result_str = str(result)
                partial_content += result_str
                return str(result)
                
        except Exception as e:
            execution_time = asyncio.get_event_loop().time() - execution_start_time
            logger.error(f"Error running MCP tool {self.mcp_tool_name}: {str(e)}")
            
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
