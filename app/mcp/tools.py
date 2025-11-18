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
from langchain_classic.tools import BaseTool
from langchain_classic.callbacks.manager import CallbackManagerForToolRun, AsyncCallbackManagerForToolRun

from app.mcp.manager import get_mcp_manager
from app.utils.logging_utils import logger
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE

# Global counter for tracking tool execution order and implementing progressive delays
_tool_execution_counter = 0
_tool_execution_lock = asyncio.Lock()
_conversation_tool_states = {}  # Track state per conversation
# Global timeout tracking for consecutive timeouts
_consecutive_timeouts = {}
_timeout_lock = asyncio.Lock()

# Base delay in seconds for progressive throttling
# Can be configured via environment variable
BASE_DELAY_SECONDS = int(os.environ.get("MCP_TOOL_DELAY_SECONDS", "5"))

# Maximum number of sequential MCP commands per request cycle
MAX_SEQUENTIAL_TOOLS = int(os.environ.get("MCP_MAX_SEQUENTIAL_TOOLS", "20"))  # Increase default from 10 to 20

# Maximum size for tool output (in characters)
MAX_TOOL_OUTPUT_SIZE = int(os.environ.get("MCP_MAX_TOOL_OUTPUT_SIZE", "10000"))

def _get_conversation_id() -> str:
    """Get current conversation ID from global state."""
    try:
        import app.utils.custom_bedrock as custom_bedrock_module
        return getattr(custom_bedrock_module, '_current_conversation_id', 'default')
    except (ImportError, AttributeError):
        return 'default'

async def _reset_counter_async():
    """Reset the tool execution counter asynchronously."""
    global _tool_execution_counter
    conversation_id = _get_conversation_id()
    async with _tool_execution_lock:
        _tool_execution_counter = 0
        _consecutive_timeouts.clear()  # Reset timeout tracking on new request cycle
        # Reset conversation-specific state
        if conversation_id in _conversation_tool_states:
            _conversation_tool_states[conversation_id] = {
                'failed_tools': set(),
                'last_reset': time.time()
            }
        logger.info("🔄 MCP Tool counter reset for new request cycle")

def parse_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """
    Parse tool calls from content, supporting multiple formats.
    
    Supports multiple formats:
    - {TOOL_SENTINEL_OPEN}<n>tool_name</n><arguments>...</arguments>{TOOL_SENTINEL_CLOSE}
    - {TOOL_SENTINEL_OPEN}<invoke name="tool_name"><parameter name="param">value</parameter></invoke>{TOOL_SENTINEL_CLOSE}
    
    Returns:
        Dict with tool_name and arguments, or None if no valid tool call found
    """
    logger.error(f"🚨 PARSE_TOOL_CALL: Attempting to parse content length={len(content)}")
    logger.error(f"🚨 PARSE_TOOL_CALL: Content preview: {content[:200]}...")
    import json
    
    logger.debug(f"🔍 PARSE: Parsing tool call from content: {content[:200]}...")

    # Use the actual sentinel values from config
    sentinel_open_escaped = re.escape(TOOL_SENTINEL_OPEN)
    sentinel_close_escaped = re.escape(TOOL_SENTINEL_CLOSE)
    
    # Format 1: Handle both <name> and <n> formats
    # Pattern: <TOOL_SENTINEL><name>tool_name</name><arguments>{...}</arguments></TOOL_SENTINEL>
    # Pattern: <TOOL_SENTINEL><n>tool_name</n><arguments>{...}</arguments></TOOL_SENTINEL>
    complete_pattern = f'{sentinel_open_escaped}\\s*<(?:name|n)>([^<]+)</(?:name|n)>\\s*<arguments>\\s*(\\{{.*?\\}})\\s*</arguments>\\s*{sentinel_close_escaped}'
    match = re.search(complete_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE_DEBUG: Raw arguments string: '{match.group(2)}'")
            logger.debug(f"🔍 PARSE_DEBUG: Parsed arguments: {arguments}")
            print(f"🔍 PARSE_DEBUG: Raw arguments string: '{match.group(2)}', Parsed: {arguments}")
            logger.debug(f"🔍 PARSE: Successfully parsed tool format - tool: {tool_name}, args: {arguments}")
            logger.debug(f"🔍 PARSE SUCCESS: tool_name='{tool_name}', arguments={arguments}")
            print(f"🔍 PARSE SUCCESS: tool_name='{tool_name}', arguments={arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError as e:
            # Try to fix common JSON parsing issues with shell commands
            try:
                # Extract the raw arguments string and attempt to repair it
                args_str = match.group(2)
                logger.debug(f"🔍 PARSE_DEBUG: JSON parsing failed, attempting repair on: '{args_str}'")
                print(f"🔍 PARSE_DEBUG: JSON parsing failed, attempting repair on: '{args_str}'")
                repaired_args = _repair_json_arguments(args_str)
                logger.debug(f"🔍 PARSE_DEBUG: Repaired arguments: '{repaired_args}'")
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE REPAIRED: tool_name='{tool_name}', arguments={arguments}")
                print(f"🔍 PARSE REPAIRED: tool_name='{tool_name}', arguments={arguments}")
                logger.debug(f"🔍 PARSE: Successfully parsed repaired JSON - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as repair_error:
                logger.error(f"🔍 PARSE_DEBUG: Both original and repair parsing failed: {repair_error}")
                print(f"🔍 PARSE_DEBUG: Both original and repair parsing failed: {repair_error}")
            logger.warning(f"Failed to parse JSON arguments for tool {tool_name}: {e}")
            return None
    # Pattern: <TOOL_SENTINEL><name>tool_name</name><arguments>{...}</arguments></TOOL_SENTINEL>
    complete_name_pattern = f'{sentinel_open_escaped}\\s*<name>([^<]+)</name>\\s*<arguments>\\s*(\\{{.*?\\}})\\s*</arguments>\\s*{sentinel_close_escaped}'
    match = re.search(complete_name_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed complete <name> format - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            # Try to fix common JSON parsing issues with shell commands
            try:
                # Extract the raw arguments string and attempt to repair it
                args_str = match.group(2)
                repaired_args = _repair_json_arguments(args_str)
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE: Successfully parsed repaired JSON - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.warning(f"Failed to parse JSON arguments for tool {tool_name}: {e}")
                return None

    # Format 2: <n> format without closing tag (partial/streaming)
    # Pattern: <TOOL_SENTINEL><n>tool_name</n><arguments>{...}</arguments>
    partial_pattern = f'{sentinel_open_escaped}\\s*<n>([^<]+)</n>\\s*<arguments>\\s*(\\{{.*?\\}})\\s*</arguments>'
    match = re.search(partial_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed partial <n> format - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            # Try to fix common JSON parsing issues with shell commands
            try:
                # Extract the raw arguments string and attempt to repair it
                args_str = match.group(2)
                repaired_args = _repair_json_arguments(args_str)
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE: Successfully parsed repaired JSON - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.warning(f"Failed to parse JSON arguments for tool {tool_name}: {e}")
                return None

    # Format 2b: <name> format without closing tag (partial/streaming)
    # Pattern: <TOOL_SENTINEL><name>tool_name</name><arguments>{...}</arguments>
    partial_name_pattern = f'{sentinel_open_escaped}\\s*<name>([^<]+)</name>\\s*<arguments>\\s*(\\{{.*?\\}})\\s*</arguments>'
    match = re.search(partial_name_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            # Try to fix common JSON parsing issues with shell commands
            try:
                # Extract the raw arguments string and attempt to repair it
                args_str = match.group(2)
                repaired_args = _repair_json_arguments(args_str)
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE: Successfully parsed repaired JSON - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.warning(f"Failed to parse JSON arguments for tool {tool_name}: {e}")
                return None

    # Format 3: <name> format (alternative to <n>)
    # Pattern: <TOOL_SENTINEL><name>tool_name</name><arguments>{...}</arguments></TOOL_SENTINEL>
    name_pattern = f'{sentinel_open_escaped}\\s*<name>([^<]+)</name>\\s*<arguments>\\s*(\\{{.*?\\}})\\s*</arguments>\\s*{sentinel_close_escaped}'
    match = re.search(name_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed <name> format - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            # Try to fix common JSON parsing issues with shell commands
            try:
                # Extract the raw arguments string and attempt to repair it
                args_str = match.group(2)
                logger.debug(f"🔍 PARSE_DEBUG: JSON parsing failed for <name> format, attempting repair on: '{args_str}'")
                repaired_args = _repair_json_arguments(args_str)
                logger.debug(f"🔍 PARSE_DEBUG: Repaired arguments for <name> format: '{repaired_args}'")
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE REPAIRED: tool_name='{tool_name}', arguments={arguments}")
                logger.debug(f"🔍 PARSE: Successfully parsed repaired <name> format - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.error(f"🔍 PARSE_DEBUG: Both original and repair parsing failed for <name> format: {e}")
                logger.warning(f"Failed to parse JSON arguments for tool {tool_name}: {e}")
                return None

    # Format 4: <invoke> format (alternative format)
    # Pattern: <TOOL_SENTINEL><invoke name="tool_name"><parameter name="param">value</parameter></invoke></TOOL_SENTINEL>
    invoke_pattern = f'{sentinel_open_escaped}\\s*<invoke\\s+name="([^"]+)"[^>]*>(.*?)</invoke>\\s*{sentinel_close_escaped}'
    match = re.search(invoke_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        invoke_content = match.group(2)
        
        # Parse parameters from invoke content
        arguments = {}
        param_pattern = r'<parameter\s+name="([^"]+)"[^>]*>(.*?)</parameter>'
        param_matches = re.findall(param_pattern, invoke_content, re.DOTALL)
        
        for param_name, param_value in param_matches:
            # Try to parse as JSON, otherwise use as string
            try:
                arguments[param_name] = json.loads(param_value.strip())
            except json.JSONDecodeError:
                arguments[param_name] = param_value.strip()
        
        logger.debug(f"🔍 PARSE: Successfully parsed invoke format - tool: {tool_name}, args: {arguments}")
        return {"tool_name": tool_name, "arguments": arguments}

    # Format 4: <n> format with JSON directly after (no <arguments> tags)
    # Pattern: <TOOL_SENTINEL><n>tool_name</n>{...}</TOOL_SENTINEL>
    direct_json_pattern = f'{sentinel_open_escaped}\\s*<n>([^<]+)</n>\\s*(\\{{.*?\\}})\\s*{sentinel_close_escaped}'
    match = re.search(direct_json_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed direct JSON <n> format - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            try:
                args_str = match.group(2)
                repaired_args = _repair_json_arguments(args_str)
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE: Successfully parsed repaired direct JSON - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.warning(f"Failed to parse direct JSON arguments for tool {tool_name}: {e}")
                return None

    # Format 4a: <name> format with <arguments> tags
    # Pattern: <TOOL_SENTINEL><name>tool_name</name><arguments>{...}</arguments></TOOL_SENTINEL>
    name_args_pattern = f'{sentinel_open_escaped}\\s*<name>([^<]+)</name>\\s*<arguments>\\s*(\\{{.*?\\}})\\s*</arguments>\\s*{sentinel_close_escaped}'
    match = re.search(name_args_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed <name> format - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            try:
                args_str = match.group(2)
                repaired_args = _repair_json_arguments(args_str)
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE: Successfully parsed repaired <name> format - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.warning(f"Failed to parse <name> format arguments for tool {tool_name}: {e}")
                return None

    # Format 4b: <name> format with JSON directly after (no <arguments> tags)
    # Pattern: <TOOL_SENTINEL><name>tool_name</name>{...}</TOOL_SENTINEL>
    name_direct_pattern = f'{sentinel_open_escaped}\\s*<name>([^<]+)</name>\\s*(\\{{.*?\\}})\\s*{sentinel_close_escaped}'
    match = re.search(name_direct_pattern, content, re.DOTALL)
    if match:
        tool_name = match.group(1).strip()
        try:
            arguments = json.loads(match.group(2))
            logger.debug(f"🔍 PARSE: Successfully parsed direct JSON <name> format - tool: {tool_name}, args: {arguments}")
            return {"tool_name": tool_name, "arguments": arguments}
        except json.JSONDecodeError:
            try:
                args_str = match.group(2)
                repaired_args = _repair_json_arguments(args_str)
                arguments = json.loads(repaired_args)
                logger.debug(f"🔍 PARSE: Successfully parsed repaired direct JSON <name> - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except Exception as e:
                logger.warning(f"Failed to parse direct JSON <name> arguments for tool {tool_name}: {e}")
                return None

    # Format 5: Simple <n> extraction (fallback)
    # Look for just the tool name and try to find arguments separately
    simple_name_pattern = r'<n>([^<]+)</n>'
    name_match = re.search(simple_name_pattern, content)
    if name_match:
        tool_name = name_match.group(1).strip()
        
        # Try to extract arguments
        args_pattern = r'<arguments>\s*(\{.*?\})\s*</arguments>'
        args_match = re.search(args_pattern, content, re.DOTALL)
        if args_match:
            try:
                arguments = json.loads(args_match.group(1))
                logger.debug(f"🔍 PARSE: Successfully parsed simple <n> format - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except json.JSONDecodeError:
                pass
        
        # Return with empty arguments if no valid JSON found
        logger.debug(f"🔍 PARSE: Parsed tool name only from <n> - tool: {tool_name}")
        return {"tool_name": tool_name, "arguments": {}}

    # Format 4b: Simple <name> extraction (fallback)
    # Look for just the tool name and try to find arguments separately
    simple_name_pattern_alt = r'<name>([^<]+)</name>'
    name_match = re.search(simple_name_pattern_alt, content)
    if name_match:
        tool_name = name_match.group(1).strip()
        
        # Try to extract arguments
        args_pattern = r'<arguments>\s*(\{.*?\})\s*</arguments>'
        args_match = re.search(args_pattern, content, re.DOTALL)
        if args_match:
            try:
                arguments = json.loads(args_match.group(1))
                logger.debug(f"🔍 PARSE: Successfully parsed simple <name> format - tool: {tool_name}, args: {arguments}")
                return {"tool_name": tool_name, "arguments": arguments}
            except json.JSONDecodeError:
                pass
        
        # Return with empty arguments if no valid JSON found
        logger.debug(f"🔍 PARSE: Parsed tool name only from <name> - tool: {tool_name}")
        return {"tool_name": tool_name, "arguments": {}}

    # If we get here, no valid tool call was found
    if TOOL_SENTINEL_OPEN in content:
        logger.warning(f"🔍 PARSE: Tool sentinel found but could not parse tool call from: {content[:300]}...")
    
    return None

def _repair_json_arguments(args_str: str) -> str:
    """
    Repair common JSON parsing issues, especially with shell commands containing quotes and backslashes.
    
    This function handles several common issues:
    1. Unescaped quotes within string values (especially complex shell commands)
    2. Unescaped backslashes
    3. Missing quotes around property names
    4. Trailing commas
    5. Missing quotes around string values
    
    Args:
        args_str: The raw JSON arguments string
        
    Returns:
        Repaired JSON string
    """
    
    # Step 1: Handle complex command field issues (like printf statements)
    # This uses a more robust approach for complex shell commands
    command_start_pattern = r'"command"\s*:\s*"'
    match = re.search(command_start_pattern, args_str)
    
    if match:
        start_pos = match.end()
        
        # Find the end of the command value by looking for patterns like:
        # ", "timeout" or ", "other_field" or "}
        end_patterns = [r'",\s*"timeout"', r'",\s*"[^"]*"', r'"\s*}']
        
        end_pos = None
        for pattern in end_patterns:
            end_match = re.search(pattern, args_str[start_pos:])
            if end_match:
                end_pos = start_pos + end_match.start()
                break
        
        if end_pos is None:
            # Fallback: assume the command goes to near the end
            end_pos = len(args_str) - 2  # Before the closing }
        
        # Extract the command value
        command_value = args_str[start_pos:end_pos]
        
        # Check if the command has complex issues (like unescaped quotes in printf/awk)
        if '"' in command_value and ('printf' in command_value or 'awk' in command_value or len(command_value) > 100):
            # Use aggressive escaping for complex shell commands
            escaped_command = command_value.replace('\\', '\\\\').replace('"', '\\"')
            args_str = args_str[:start_pos] + escaped_command + args_str[end_pos:]
            logger.debug(f"🔧 REPAIR: Fixed complex shell command escaping: {command_value[:50]}...")
    
    # Step 2: Handle other JSON issues with original logic
    
    # Fix missing quotes around property names
    args_str = re.sub(r'(\{|\,)\s*([a-zA-Z0-9_]+)\s*:', r'\1"\2":', args_str)
    
    # Fix trailing commas
    args_str = re.sub(r',\s*}', '}', args_str)
    
    # Fix missing quotes around string values
    def fix_unquoted_values(match):
        key = match.group(1)
        value = match.group(2).strip()
        # Don't add quotes if it looks like a number or boolean
        if re.match(r'^-?\d+(\.\d+)?$', value) or value in ('true', 'false', 'null'):
            return f'"{key}": {value}'
        else:
            return f'"{key}": "{value}"'
    
    args_str = re.sub(r'"([^"]+)"\s*:\s*([^",{}\[\]]+)(?=,|})', fix_unquoted_values, args_str)
    
    # Handle special case where the entire JSON might be malformed
    if not args_str.strip().startswith('{'):
        # Try to extract a command value and create a proper JSON object
        command_match = re.search(r'([^"{}]+)', args_str)
        if command_match:
            command_text = command_match.group(1).strip()
            args_str = f'{{"command": "{command_text}"}}'
            logger.debug(f"🔧 REPAIR: Created JSON object from raw text: {command_text[:50]}...")
    
    logger.debug(f"🔧 REPAIR: Final repaired JSON: {args_str}")
    return args_str
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
        _consecutive_timeouts.pop(tool_name, None)

class MCPTool(BaseTool):
    """
    LangChain tool wrapper for MCP tools.
    
    This allows MCP tools to be used seamlessly within Ziya's agent system.
    """
    
    name: str
    description: str
    mcp_tool_name: str
    
    def _run(
        self,
        run_manager: Optional[CallbackManagerForToolRun] = None,
        **kwargs: Any,
    ) -> str:
        """Run the MCP tool synchronously."""
        logger.info(f"MCPTool._run called for {self.mcp_tool_name} with args: {kwargs}")
        
        # Check if we're already in an event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, we need to create a task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self._arun(run_manager=run_manager, **kwargs))
                    return future.result()
            else:
                return loop.run_until_complete(self._arun(run_manager=run_manager, **kwargs))
        except RuntimeError:
            # No event loop, safe to create one
            return asyncio.run(self._arun(run_manager=run_manager, **kwargs))
    
    async def _arun(
        self,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        **kwargs: Any,
    ) -> str:
        """Run the MCP tool asynchronously."""
        arguments = kwargs
        logger.info(f"MCPTool._arun called for {self.mcp_tool_name} with args: {arguments}")
        logger.debug(f"🔍 MCPTool._arun: About to execute MCP tool {self.mcp_tool_name}")
        logger.debug(f"🔍 MCPTool._arun: MCP manager initialized: {mcp_manager.is_initialized if 'mcp_manager' in globals() else 'No manager'}")
        
        # Implement progressive delay to prevent Bedrock throttling
        global _tool_execution_counter
        async with _tool_execution_lock:
            # Check if we've hit the sequential tool limit
            if _tool_execution_counter >= MAX_SEQUENTIAL_TOOLS:
                logger.warning(f"🚫 TOOL LIMIT: Hit sequential limit of {MAX_SEQUENTIAL_TOOLS} tools, blocking '{self.mcp_tool_name}' (counter={_tool_execution_counter})")
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
        
        # For shell commands, show the actual command being executed
        if self.mcp_tool_name == "run_shell_command" and "command" in arguments:
            actual_command = arguments["command"]
            status_parts.append(f"🔧 **Shell Command**")
            status_parts.append(f"⏳ **Running**: {actual_command}")
        # For workspace search, show the actual search query and type
        elif self.mcp_tool_name == "WorkspaceSearch" and "searchQuery" in arguments:
            search_query = arguments["searchQuery"]
            search_type = arguments.get("searchType", "contentLiteral")
            status_parts.append(f"🔍 **Workspace Search**")
            status_parts.append(f"⏳ **Searching**: \"{search_query}\" ({search_type})")
        else:
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
            
            logger.debug(f"🔍 MCPTool._arun: Got result from MCP manager: {result}")
            
            # Reset timeout counter on successful execution
            await _reset_timeout_counter(self.mcp_tool_name)
            
            # Log successful execution for throttling analysis
            logger.info(f"✅ MCP Tool execution completed: {self.mcp_tool_name} (execution #{current_execution_order + 1}, delay was {delay_seconds}s)")
            
            # Check if this is an error response from the MCP server
            if isinstance(result, dict) and result.get("error"):
                error_msg = result.get("message", "Unknown MCP error")
                error_code = result.get("code", -1)
                
                # For validation errors, provide clearer context and don't retry
                if "validation" in error_msg.lower() or error_code == -32602:
                    return f"❌ **Parameter Validation Error**: {error_msg}\n\nPlease check the tool's parameter requirements and try again with correct parameter types."
                
                
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
