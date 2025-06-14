"""
MCP-specific prompt extensions.

This module contains prompt extensions that provide guidance on how to interact
with MCP (Model Context Protocol) servers and tools.
"""

from app.utils.prompt_extensions import prompt_extension
from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
from app.utils.logging_utils import logger

logger.info("MCP_GUIDELINES: mcp_prompt_extensions.py module being imported")

@prompt_extension(
    name="mcp_usage_guidelines",
    extension_type="global",
    config={
        "enabled": True,
        "priority": 15
    }
)
def mcp_usage_guidelines(prompt: str, context: dict) -> str:
    """
    Add MCP usage guidelines to the system prompt when MCP tools are available.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt with MCP guidelines
    """
    logger.info("MCP_GUIDELINES: @prompt_extension decorator applied to mcp_usage_guidelines")
    logger.info("MCP_GUIDELINES: mcp_usage_guidelines function called")
    if not context.get("config", {}).get("enabled", True):
        logger.info("MCP_GUIDELINES: Extension disabled by config, returning original prompt")
        return prompt
    
    # Check if MCP tools are available in the context
    # This would be passed from the agent system when MCP is initialized
    mcp_tools_available = context.get("mcp_tools_available", False)
    available_tools = context.get("available_mcp_tools", [])
    
    if not mcp_tools_available or not available_tools:
        logger.info("MCP_GUIDELINES: No MCP tools available or list is empty, returning original prompt.") # ADD THIS
        return prompt
    
    mcp_guidelines = """

## MCP Tool Usage - CRITICAL INSTRUCTIONS
**EXECUTE TOOLS WHEN REQUESTED - Never simulate or describe what you would do.**

**Available Tools:**
""" + _get_tool_descriptions_from_mcp(available_tools) + """

""" + _get_tool_call_formats_from_mcp(available_tools) + """

**Usage Rules:**
0. **Prefer local context and AST over tools when either can provide similar information**
1. **Always use actual tool results** - Never fabricate output
2. **Shell commands**: Use read-only commands (ls, cat, grep) when possible; format output as terminal session
3. **Time queries**: Always use tool rather than guessing current time
4. **Error handling**: Show actual errors and try alternatives
5. **Verification**: Use tools to verify system state rather than making assumptions    
"""

    logger.info(f"MCP_GUIDELINES: Original prompt length: {len(prompt)}") # ADD THIS
    logger.info(f"MCP_GUIDELINES: Appending guidelines. Available tools: {available_tools}") # ADD THIS
    modified_prompt = prompt + mcp_guidelines
    logger.info(f"MCP_GUIDELINES: Modified prompt length: {len(modified_prompt)}") # ADD THIS
    logger.info(f"MCP_GUIDELINES: Last 500 chars of modified prompt: ...{modified_prompt[-500:]}") # ADD THIS
    return modified_prompt

def _get_tool_description(tool_name: str) -> str:
    """Get a brief description of what an MCP tool is used for."""
    descriptions = {
        "mcp_get_current_time": "checking current system time and date",
        "mcp_run_shell_command": "executing safe shell commands to inspect system state", 
    }
    return descriptions.get(tool_name, "specialized system operations")

def _get_tool_descriptions_from_mcp(available_tools: list) -> str:
    """Get tool descriptions from actual MCP tool definitions."""
    tool_descriptions = []
    
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        if mcp_manager.is_initialized:
            # Get all MCP tools with their descriptions
            mcp_tools = mcp_manager.get_all_tools()
            tool_map = {tool.name: tool.description for tool in mcp_tools}
            
            for tool_name in available_tools:
                # Handle both prefixed and non-prefixed tool names
                clean_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
                description = tool_map.get(clean_name, "Specialized system operations")
                
                display_name = f"mcp_{clean_name}" if not tool_name.startswith("mcp_") else tool_name
                tool_descriptions.append(f"- **{display_name}**: {description}")
        else:
            # Fallback if MCP manager not initialized
            for tool_name in available_tools:
                display_name = f"mcp_{tool_name}" if not tool_name.startswith("mcp_") else tool_name
                tool_descriptions.append(f"- **{display_name}**: Specialized system operations")
                
    except Exception as e:
        logger.warning(f"Could not get MCP tool descriptions: {e}")
        # Fallback to generic descriptions
        for tool_name in available_tools:
            display_name = f"mcp_{tool_name}" if not tool_name.startswith("mcp_") else tool_name
            tool_descriptions.append(f"- **{display_name}**: Specialized system operations")
    
    return "\n".join(tool_descriptions)

def _get_tool_call_formats_from_mcp(available_tools: list) -> str:
    """Generate tool call format examples from actual MCP tool schemas."""
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return _get_fallback_tool_formats(available_tools)
            
        # Get all MCP tools with their schemas
        mcp_tools = mcp_manager.get_all_tools()
        tool_schemas = {tool.name: tool.inputSchema for tool in mcp_tools}
        
        format_sections = []
        
        for tool_name in available_tools:
            clean_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            display_name = f"mcp_{clean_name}" if not tool_name.startswith("mcp_") else tool_name
            
            schema = tool_schemas.get(clean_name)
            if schema and "properties" in schema:
                # Generate example arguments from schema
                example_args = _generate_example_args_from_schema(schema, clean_name)
                
                format_sections.append(f"""**{display_name} Format:**
```
{TOOL_SENTINEL_OPEN}
<name>{display_name}</name>
<arguments>{example_args}</arguments>
{TOOL_SENTINEL_CLOSE}
```""")
        
        if format_sections:
            return "\n\n".join(format_sections)
        else:
            return _get_fallback_tool_formats(available_tools)
            
    except Exception as e:
        logger.warning(f"Could not get MCP tool schemas: {e}")
        return _get_fallback_tool_formats(available_tools)
 
def _generate_example_args_from_schema(schema: dict, tool_name: str) -> str:
    """Generate example arguments JSON from tool schema."""
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    
    example_args = {}
    for prop_name, prop_info in properties.items():
        if prop_name in required or len(properties) <= 2:  # Include all if few properties
            example_value = _get_example_value_for_property(prop_info, prop_name, tool_name)
            example_args[prop_name] = example_value
    
    import json
    # Escape curly braces for template formatting
    json_str = json.dumps(example_args, indent=2)
    # Double the braces to escape them in Python string formatting
    escaped_json = json_str.replace('{', '{{').replace('}', '}}')
    return escaped_json
 
def _get_example_value_for_property(prop_info: dict, prop_name: str, tool_name: str) -> str:
    """Generate appropriate example value based on property info and context."""
    prop_type = prop_info.get("type", "string")
    description = prop_info.get("description", "").lower()
    
    # Tool-specific examples
    if tool_name == "run_shell_command" and prop_name == "command":
        return "ls -la"
    elif tool_name == "get_current_time" and prop_name == "format":
        return "readable"
    
    # Generic examples based on type and description
    if prop_type == "string":
        if "command" in description or prop_name == "command":
            return "your_command_here"
        elif "format" in description or prop_name == "format":
            return "readable"
        else:
            return f"your_{prop_name}_here"
    elif prop_type == "boolean":
        return "true"
    elif prop_type == "number" or prop_type == "integer":
        return "1"
    else:
        return f"your_{prop_name}_here"
 
def _get_fallback_tool_formats(available_tools: list) -> str:
    """Fallback tool format examples when schema info isn't available."""
    formats = []
    
    for tool_name in available_tools:
        clean_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
        display_name = f"mcp_{clean_name}" if not tool_name.startswith("mcp_") else tool_name
        
        if clean_name == "run_shell_command":
            example_args = '{{"command": "ls -la"}}'
        elif clean_name == "get_current_time":
            example_args = '{{"format": "readable"}}'
        else:
            example_args = '{{"key": "value"}}'
            
        formats.append(f"""**{display_name} Format:**
```
{TOOL_SENTINEL_OPEN}
<name>{display_name}</name>
<arguments>{example_args}</arguments>
{TOOL_SENTINEL_CLOSE}
```""")
    
    return "\n\n".join(formats)

def register_extensions(manager):
    """
    Register all extensions in this module with the extension manager.
    
    Args:
        manager: The PromptExtensionManager instance
    """
    # Extensions are registered via decorators
    pass
