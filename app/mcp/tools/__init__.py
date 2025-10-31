"""
MCP Tools module initialization.

This module provides the conversation and folder management tools that allow
the model to create and organize conversations for complex multi-threaded tasks.
"""

from typing import List, Type, Optional, Dict, Any
from langchain_core.tools import BaseTool

from app.utils.logging_utils import logger

# Import conversation management tools
try:
    from app.mcp.tools.conversation_management import (
        CreateFolderTool, 
        CreateConversationTool, 
        ListFoldersAndConversationsTool, 
        MoveConversationTool
    )
    CONVERSATION_TOOLS_AVAILABLE = True
    logger.info("Conversation management tools loaded successfully")
except ImportError as e:
    logger.warning(f"Could not import conversation management tools: {e}")
    CONVERSATION_TOOLS_AVAILABLE = False
    # Define empty classes as placeholders
    class CreateFolderTool: pass
    class CreateConversationTool: pass  
    class ListFoldersAndConversationsTool: pass
    class MoveConversationTool: pass


# List of all available MCP tools for conversation management
CONVERSATION_MANAGEMENT_TOOLS: List[Type] = []

if CONVERSATION_TOOLS_AVAILABLE:
    CONVERSATION_MANAGEMENT_TOOLS = [
        CreateFolderTool,
        CreateConversationTool,
        ListFoldersAndConversationsTool,
        MoveConversationTool,
    ]
    logger.info(f"Registered {len(CONVERSATION_MANAGEMENT_TOOLS)} conversation management tools")
else:
    logger.warning("Conversation management tools not available - tools will not be registered")


def create_mcp_tools() -> List[BaseTool]:
    """
    Create MCP tools for LangChain integration.
    This function is expected by the existing agent system.
    """
    try:
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        return create_secure_mcp_tools()
    except ImportError as e:
        logger.warning(f"Could not import enhanced MCP tools: {e}")
        return []


def parse_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """
    Parse tool calls from content.
    This function is expected by the existing agent system.
    """
    try:
        # Look for tool call patterns in the content
        # Handle both <TOOL_SENTINEL> format and other formats
        
        # Pattern 1: <TOOL_SENTINEL><n>tool_name</n><arguments>...</arguments></TOOL_SENTINEL>
        tool_pattern = r'<TOOL_SENTINEL>\s*<n>(.*?)</n>\s*<arguments>(.*?)</arguments>\s*</TOOL_SENTINEL>'
        match = re.search(tool_pattern, content, re.DOTALL)
        
        if match:
            tool_name = match.group(1).strip()
            args_text = match.group(2).strip()
            
            try:
                arguments = json.loads(args_text)
            except json.JSONDecodeError:
                # Try to parse as key=value pairs if not JSON
                arguments = {}
                for line in args_text.split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        arguments[key.strip()] = value.strip().strip('"')
            
            return {
                "tool_name": tool_name,
                "arguments": arguments,
                "raw_match": match.group(0)
            }
        
        # Pattern 2: <name>tool_name</name><arguments>...</arguments>
        name_pattern = r'<name>(.*?)</name>\s*<arguments>(.*?)</arguments>'
        match = re.search(name_pattern, content, re.DOTALL)
        
        if match:
            tool_name = match.group(1).strip()
            args_text = match.group(2).strip()
            
            try:
                arguments = json.loads(args_text)
            except json.JSONDecodeError:
                arguments = {"input": args_text}
            
            return {
                "tool_name": tool_name,
                "arguments": arguments,
                "raw_match": match.group(0)
            }
        
        # Pattern 3: Simple tool invocation <tool_name>args</tool_name>
        simple_pattern = r'<(\w+)>(.*?)</\1>'
        match = re.search(simple_pattern, content, re.DOTALL)
        
        if match:
            tool_name = match.group(1)
            args_content = match.group(2).strip()
            
            return {
                "tool_name": tool_name,
                "arguments": {"input": args_content} if args_content else {},
                "raw_match": match.group(0)
            }
        
        return None
        
    except Exception as e:
        logger.error(f"Error parsing tool call: {e}")
        return None


# Export the tools and availability flag
__all__ = [
    "CONVERSATION_MANAGEMENT_TOOLS",
    "CONVERSATION_TOOLS_AVAILABLE",
    "CreateFolderTool",
    "CreateConversationTool", 
    "ListFoldersAndConversationsTool",
    "MoveConversationTool",
    "create_mcp_tools",
    "parse_tool_call"
]
