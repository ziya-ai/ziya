"""
Consolidated MCP system with simplified tool execution.

This module provides the single entry point for MCP tool execution with:
- Direct execution path for tool calls
- Improved parsing and error handling
- Proper result formatting and insertion
"""

import json
import asyncio
import time
import re
from typing import Optional, Dict, Any, Tuple, List

from app.utils.logging_utils import logger


async def execute_mcp_tools_with_status(full_response: str) -> str:
    """
    Unified MCP tool execution with simplified execution path.
    
    This function:
    1. Identifies tool calls in the response
    2. Executes the tools directly
    3. Replaces the tool calls with the results
    4. Returns the modified response
    """
    print(f"🔧 DEBUG: execute_mcp_tools_with_status called with response length {len(full_response)}")
    logger.info(f"🔧 MCP: Processing response ({len(full_response)} chars)")
    
    # Import improved functions
    from app.mcp.utils import clean_sentinels
    from app.mcp.enhanced_tools import process_enhanced_triggers
    from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
    
    # First process any enhanced triggers (secure execution)
    processed_response = await process_enhanced_triggers(full_response, "default")
    
    # Check if response contains standard sentinel tool calls
    has_standard_tool_calls = (
        TOOL_SENTINEL_OPEN in full_response and TOOL_SENTINEL_CLOSE in full_response
    ) or (
        "<TOOL_SENTINEL>" in full_response and "</TOOL_SENTINEL>" in full_response
    )
    
    # Check if response contains XML-style tool calls
    import re
    xml_tool_pattern = r'<([a-zA-Z_][a-zA-Z0-9_]*)>.*?</\1>'
    has_xml_tool_calls = bool(re.search(xml_tool_pattern, full_response, re.DOTALL))
    
    # Check if response contains invoke-style tool calls
    invoke_pattern = r'<invoke\s+name="[^"]+">'
    has_invoke_tool_calls = bool(re.search(invoke_pattern, full_response, re.DOTALL))
    
    # If no tool calls found, return the cleaned response
    if not (has_standard_tool_calls or has_xml_tool_calls or has_invoke_tool_calls):
        # Clean any stray sentinels before returning
        cleaned_response = clean_sentinels(full_response)
        if cleaned_response != full_response:
            logger.info("🔧 MCP: Cleaned stray sentinels from response")
        return cleaned_response
    
    # For multiple tool calls, use find_and_execute_all_tools
    if (has_standard_tool_calls and has_xml_tool_calls) or \
       (has_standard_tool_calls and has_invoke_tool_calls) or \
       (has_xml_tool_calls and has_invoke_tool_calls) or \
       full_response.count("<get_current_time>") > 1 or \
       full_response.count("<run_shell_command>") > 1 or \
       full_response.count(TOOL_SENTINEL_OPEN) > 1 or \
       full_response.count("<TOOL_SENTINEL>") > 1 or \
       full_response.count("<invoke") > 1:
        try:
            logger.info("🔧 MCP: Detected multiple tool calls, using batch execution")
            cleaned_response, tool_results = await find_and_execute_all_tools(full_response)
            
            if tool_results:
                logger.info(f"🔧 MCP: Batch execution found and executed {len(tool_results)} tools")
                # Double clean for better sentinel removal
                return clean_sentinels(clean_sentinels(cleaned_response))
            else:
                logger.warning("🔧 MCP: Batch execution found no valid tools")
                # Fall back to direct execution
        except Exception as batch_error:
            logger.error(f"🔧 MCP: Batch execution failed: {batch_error}")
            # Fall back to direct execution
    
    # Try direct execution for single tool call
    try:
        logger.info("🔧 MCP: Executing tool directly")
        result = await _execute_direct_mcp_tools(full_response)
        
        # Double clean any remaining sentinels for better removal
        cleaned_result = clean_sentinels(clean_sentinels(result))
        if cleaned_result != result:
            logger.info("🔧 MCP: Cleaned remaining sentinels after execution")
            result = cleaned_result
        
        logger.info(f"🔧 MCP: Execution successful, final length: {len(result)}")
        return result
        
    except Exception as error:
        logger.error(f"🔧 MCP: Tool execution failed: {error}")
        
        # Don't retry with find_and_execute_all_tools again as it was already tried above
        # This prevents duplicate tool execution
        logger.error(f"🔧 MCP: Both batch and direct execution failed: {error}")
        
        # Return double-cleaned response without status badge
        return clean_sentinels(clean_sentinels(full_response))


async def _execute_direct_mcp_tools(full_response: str) -> str:
    """
    Execute MCP tools using direct connection with enhanced parsing.
    
    This function:
    1. Finds and parses tool calls from the response
    2. Executes the tools via the MCP manager
    3. Formats the results
    4. Replaces the tool calls with the results
    """
    from app.mcp.tools import parse_tool_call
    from app.mcp.manager import get_mcp_manager
    from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
    import re
    
    logger.info(f"🔧 MCP: Direct execution starting for response length {len(full_response)}")
    
    # Get MCP manager
    mcp_manager = get_mcp_manager()
    if not mcp_manager or not mcp_manager.is_initialized:
        logger.error("🔧 MCP: Manager not initialized")
        return full_response
    
    # Find all tool calls using multiple patterns
    tool_calls = []
    
    # Pattern 1: <name> format (PRIMARY - from system prompt)
    name_pattern = r'<TOOL_SENTINEL>\s*<name>[^<]+</name>\s*<arguments>\{.*?\}</arguments>\s*</TOOL_SENTINEL>'
    tool_calls.extend(re.findall(name_pattern, full_response, re.DOTALL))
    
    # Pattern 2: <name> format without closing tag
    name_partial_pattern = r'<TOOL_SENTINEL>\s*<name>[^<]+</name>\s*<arguments>\{.*?\}</arguments>'
    partial_matches = re.findall(name_partial_pattern, full_response, re.DOTALL)
    tool_calls.extend(partial_matches)
    
    # Pattern 3: <n> format (LEGACY)
    n_pattern = r'<TOOL_SENTINEL>\s*<n>[^<]+</n>\s*<arguments>\{.*?\}</arguments>\s*</TOOL_SENTINEL>'
    tool_calls.extend(re.findall(n_pattern, full_response, re.DOTALL))
    
    # Pattern 4: <n> format without closing tag
    n_partial_pattern = r'<TOOL_SENTINEL>\s*<n>[^<]+</n>\s*<arguments>\{.*?\}</arguments>'
    partial_matches = re.findall(n_partial_pattern, full_response, re.DOTALL)
    tool_calls.extend(partial_matches)
    
    # Pattern 5: Standard <TOOL_SENTINEL> format (catch-all)
    standard_pattern = r'<TOOL_SENTINEL>.*?</TOOL_SENTINEL>'
    tool_calls.extend(re.findall(standard_pattern, full_response, re.DOTALL))
    
    # Pattern 3: Custom sentinel format
    if TOOL_SENTINEL_OPEN != "<TOOL_SENTINEL>":
        custom_pattern = re.escape(TOOL_SENTINEL_OPEN) + r'.*?' + re.escape(TOOL_SENTINEL_CLOSE)
        tool_calls.extend(re.findall(custom_pattern, full_response, re.DOTALL))
    
    if not tool_calls:
        logger.info("🔧 MCP: No tool calls found in response")
        return full_response
    
    logger.info(f"🔧 MCP: Found {len(tool_calls)} tool calls to execute")
    
    modified_response = full_response
    
    for i, tool_call_block in enumerate(tool_calls):
        logger.info(f"🔧 MCP: Processing tool call {i+1}/{len(tool_calls)}")
        
        # Parse the tool call
        parsed_call = parse_tool_call(tool_call_block)
        if not parsed_call:
            logger.warning(f"🔧 MCP: Could not parse tool call {i+1}: {tool_call_block[:100]}...")
            continue
        
        tool_name = parsed_call["tool_name"]
        arguments = parsed_call["arguments"]
        
        logger.info(f"🔧 MCP: Executing {tool_name} with args: {arguments}")
        
        try:
            # Remove mcp_ prefix if present for internal lookup
            internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            
            # Execute the tool
            result = await mcp_manager.call_tool(internal_tool_name, arguments)
            
            # Handle validation errors immediately without processing
            if isinstance(result, dict) and result.get("error"):
                error_msg = result.get("message", "Unknown error")
                if "validation" in error_msg.lower():
                    error_replacement = f"\n\n❌ **Tool Validation Error**: {error_msg}\n\n"
                    modified_response = modified_response.replace(tool_call_block, error_replacement)
                    continue
                    
            if result is None:
                logger.error(f"🔧 MCP: Tool {internal_tool_name} returned None")
                continue
            
            # Format the result properly
            if isinstance(result, dict) and "content" in result:
                if isinstance(result["content"], list) and len(result["content"]) > 0:
                    tool_output = result["content"][0].get("text", str(result["content"]))
                else:
                    tool_output = str(result["content"])
            else:
                tool_output = str(result)
            
            logger.info(f"🔧 MCP: Tool executed successfully, output length: {len(tool_output)}")
            
            # Create a clean replacement
            replacement = f"\\n```tool:{tool_name}\\n{tool_output.strip()}\\n```\\n"
            
            # Replace the tool call with the result
            modified_response = modified_response.replace(tool_call_block, replacement)
            
        except Exception as e:
            logger.error(f"🔧 MCP: Error executing tool {tool_name}: {str(e)}")
            # Replace with error message
            error_msg = f"\\n**Tool Error ({tool_name}):** {str(e)}\\n"
            modified_response = modified_response.replace(tool_call_block, error_msg)
    
    logger.info(f"🔧 MCP: Direct execution complete, final length: {len(modified_response)}")
    return modified_response


async def find_and_execute_all_tools(response: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Find and execute all tool calls in a response.
    
    Args:
        response: The full response text
        
    Returns:
        Tuple of (cleaned response, list of tool results)
    """
    # Import here to avoid circular imports
    from app.mcp.manager import get_mcp_manager
    from app.mcp.utils import improved_parse_tool_call, improved_extract_tool_output, clean_sentinels
    
    # Find all tool calls
    tool_calls = []
    tool_results = []
    modified_response = response
    
    # Find all tool sentinel blocks
    start_markers = ['<TOOL_SENTINEL>', '<tool_sentinel>']
    end_markers = ['</TOOL_SENTINEL>', '</tool_sentinel>']
    
    # Also check config values
    from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
    if TOOL_SENTINEL_OPEN not in start_markers:
        start_markers.append(TOOL_SENTINEL_OPEN)
    if TOOL_SENTINEL_CLOSE not in end_markers:
        end_markers.append(TOOL_SENTINEL_CLOSE)
    
    for start_marker in start_markers:
        start_idx = 0
        while True:
            # Find the next tool call
            start_idx = modified_response.find(start_marker, start_idx)
            if start_idx == -1:
                break
                
            # Find the corresponding end marker
            end_idx = -1
            for end_marker in end_markers:
                end_idx = modified_response.find(end_marker, start_idx)
                if end_idx != -1:
                    end_idx += len(end_marker)
                    break
            
            if end_idx == -1:
                # No end marker found, move past this start marker
                start_idx += len(start_marker)
                continue
                
            # Extract the complete tool call
            tool_call = modified_response[start_idx:end_idx]
            tool_calls.append(tool_call)
            
            # Move past this tool call
            start_idx = end_idx
    
    # Also look for XML-style tool calls
    import re
    xml_tool_pattern = r'<([a-zA-Z_][a-zA-Z0-9_]*)>.*?</\1>'
    xml_matches = re.finditer(xml_tool_pattern, modified_response, re.DOTALL)
    for match in xml_matches:
        tool_call = match.group(0)
        if tool_call not in tool_calls:
            tool_calls.append(tool_call)
    
    # Also look for invoke-style tool calls
    invoke_pattern = r'<invoke\s+name="[^"]+">(.*?)</invoke>'
    invoke_matches = re.finditer(invoke_pattern, modified_response, re.DOTALL)
    for match in invoke_matches:
        tool_call = match.group(0)
        if tool_call not in tool_calls:
            tool_calls.append(tool_call)
    
    # Execute each tool call
    mcp_manager = get_mcp_manager()
    if not mcp_manager.is_initialized:
        logger.warning("MCP manager not initialized, cannot execute tools")
        return clean_sentinels(modified_response), []
    
    for tool_call in tool_calls:
        try:
            # Parse the tool call
            tool_info = improved_parse_tool_call(tool_call)
            if not tool_info:
                logger.warning(f"Could not parse tool call: {tool_call}")
                continue
            
            tool_name = tool_info['name']
            arguments = tool_info['arguments']
            
            # Execute the tool
            logger.info(f"Executing tool {tool_name} with arguments {arguments}")
            
            # Map tool name - handle both prefixed and non-prefixed versions
            internal_name = tool_name
            if tool_name.startswith("mcp_"):
                # Try with the full name first, then without prefix
                try:
                    result = await mcp_manager.call_tool(tool_name, arguments)
                except Exception as e:
                    logger.info(f"Trying without mcp_ prefix for {tool_name}")
                    internal_name = tool_name.replace("mcp_", "")
                    result = await mcp_manager.call_tool(internal_name, arguments)
            else:
                result = await mcp_manager.call_tool(internal_name, arguments)
            
            # Extract the tool output
            tool_output = improved_extract_tool_output(result)
            
            # Format the result
            formatted_result = f"\n\n```tool:{tool_name}\n{tool_output}\n```\n\n"
            
            # Replace the tool call with the result
            modified_response = modified_response.replace(tool_call, formatted_result)
            
            # Add to results
            tool_results.append({
                'tool_call': tool_call,
                'tool_name': tool_name,
                'arguments': arguments,
                'result': tool_output
            })
            
        except Exception as e:
            logger.error(f"Error executing tool: {e}")
            # Replace with error message
            error_msg = f"\n\n```tool:error\n❌ **Tool Error:** {str(e)}\n```\n\n"
            modified_response = modified_response.replace(tool_call, error_msg)
    
    # Clean any remaining sentinels
    cleaned_response = clean_sentinels(modified_response)
    
    return cleaned_response, tool_results
