"""
MCP-specific prompt extensions.

This module contains prompt extensions that provide guidance on how to interact
with MCP (Model Context Protocol) servers and tools.
"""

from app.utils.prompt_extensions import prompt_extension
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
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
    import os
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    logger.info(f"MCP_DEBUG: Checking endpoint from environment: '{endpoint}'")
    is_google_endpoint = endpoint == "google"
 
    if not context.get("config", {}).get("enabled", True):
        logger.info("MCP_GUIDELINES: Extension disabled by config, returning original prompt")
        return prompt
    
    # Skip MCP guidelines for gemini-2.5-pro to avoid prompt size limits
    # Check multiple sources for model identification
    model_id = context.get("model_id", "")
    model_name = context.get("model_name", "")
    
    # If model_id is not in context, try to get it from ModelManager
    if not model_id:
        try:
            from app.agents.models import ModelManager
            model_id = ModelManager.get_model_id() or ""
        except Exception:
            pass
    
    # Check if this is gemini-2.5-pro by any identifier
    if ("gemini-2.5-pro" in model_id or 
        "gemini-pro" in model_name or
        "gemini-2.5-pro" in str(context)):
        logger.info("MCP_GUIDELINES: Skipping for gemini-2.5-pro due to prompt size limits")
        return prompt
    
    # Check if MCP is enabled
    import os
    if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        logger.info("MCP_GUIDELINES: MCP is disabled, returning original prompt")
        return prompt
    
    # Check if MCP tools are available in the context
    # This would be passed from the agent system when MCP is initialized
    # Get server-specific tools only (exclude MCPResourceTool which is always present)
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        server_tools = mcp_manager.get_all_tools() if mcp_manager.is_initialized else []
        available_tools = [f"mcp_{tool.name}" if not tool.name.startswith("mcp_") else tool.name for tool in server_tools]
    except Exception as e:
        logger.warning(f"Could not get MCP server tools: {e}")
        available_tools = []
    
    if not available_tools:
        logger.info("MCP_GUIDELINES: No MCP tools available or list is empty, returning original prompt.")
        return prompt

 
    # For Google models, native function calling is used. Do not add XML tool instructions.
    if is_google_endpoint:
        logger.info("MCP_GUIDELINES: Google model detected. Skipping XML tool instructions in prompt.")
        return prompt
    
    # Check if native tools are available - if so, skip XML instructions
    native_tools_available = context.get("native_tools_available", False)
    if native_tools_available:
        logger.info("MCP_GUIDELINES: Native tools available. Skipping XML tool instructions in prompt.")
        return prompt
    else:
        logger.info("MCP_DEBUG: Not a Google endpoint and no native tools, adding XML tool instructions.")
        # For other models (Bedrock, etc.), provide XML-based tool instructions
        mcp_guidelines = """

🚨 CRITICAL FILE MODIFICATION PROHIBITION 🚨
═══════════════════════════════════════════════
NEVER use tools to:
- Copy files (cp, backup, etc.)
- Modify files directly (sed, awk, etc.) 
- Create new files
- Move or rename files
- Change file permissions

ONLY suggest changes through Git diff patches in your response text.
If you catch yourself about to modify a file with a tool - STOP and provide a diff instead.
═══════════════════════════════════════════════

## MCP Tool Usage - CRITICAL INSTRUCTIONS
**EXECUTE TOOLS WHEN REQUESTED - Never simulate or describe what you would do.**

**Available Tools:**
""" + _get_tool_descriptions_from_mcp(available_tools) + """

""" + _get_tool_call_formats_from_mcp(available_tools) + """

**Usage Rules:**
0. **Answer from context first** - Only use tools when you need information not available in the provided context
1. **Prefer local context and AST over tools** when either can provide similar information
2. **When using tools, use actual results** - Never fabricate output

⚠️  BEFORE EVERY TOOL CALL ASK YOURSELF: ⚠️
"Do I need information not in the context? Am I about to modify a file? If modifying files, I must provide a Git diff patch instead!"
3. **Shell commands**: Use read-only commands (ls, cat, grep) when possible; format output as terminal session
4. **Time queries**: Use tool only when current time is actually needed
5. **Error handling**: Show actual errors and try alternatives
6. **Verification**: Use tools to verify system state only when assumptions aren't sufficient
7. **No Empty Calls**: Do not generate empty or incomplete tool calls. Only output a tool call block if you have a valid command to execute.
"""

        # Add shell-specific warning if shell command tool is available
        if any("shell" in tool.lower() or "run_shell_command" in tool for tool in available_tools):
            mcp_guidelines += """

🛑 SHELL COMMAND RESTRICTIONS 🛑
Tools are for READING and ANALYZING code, not changing it.
When using shell commands, stick to read-only operations like:
- ls, find, grep, cat, head, tail, wc, du, df
- git status, git log, git show, git diff

PROHIBITED shell operations:
- File modifications: cp, mv, rm, touch, mkdir, chmod, chown
- Text editing: sed, awk with -i, nano, vim, echo >
- System changes: sudo, su, systemctl, service
"""

    logger.info(f"MCP_GUIDELINES: Original prompt length: {len(prompt)}")
    logger.info(f"MCP_GUIDELINES: Appending guidelines. Available tools: {available_tools}")
    modified_prompt = prompt + mcp_guidelines
    logger.info(f"MCP_GUIDELINES: Modified prompt length: {len(modified_prompt)}")
    logger.info(f"MCP_GUIDELINES: Last 500 chars of modified prompt: ...{modified_prompt[-500:]}")
    return modified_prompt

# Removed _get_tool_description() function as it was hardcoding shell tool descriptions
# even when shell server was disabled. Now we only show descriptions for actually enabled tools.

def _get_tool_descriptions_from_mcp(available_tools: list) -> str:
    """Get tool descriptions from actual MCP tool definitions."""
    tool_descriptions = []
    
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        if mcp_manager.is_initialized:
            # Get all MCP tools with their descriptions
            # This already filters by enabled servers only
            mcp_tools = mcp_manager.get_all_tools()
            tool_map = {tool.name: tool.description for tool in mcp_tools}
            
            for tool_name in available_tools:
                # Handle both prefixed and non-prefixed tool names
                clean_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
                description = tool_map.get(clean_name, "Specialized system operations")
                
                # Only add description if the tool is actually available from enabled servers
                if clean_name in tool_map:
                    display_name = f"mcp_{clean_name}" if not tool_name.startswith("mcp_") else tool_name
                    tool_descriptions.append(f"- **{display_name}**: {description}")
        else:
            # If MCP manager not initialized, don't show any tool descriptions
            logger.warning("MCP manager not initialized, no tool descriptions available")
            return ""
                
    except Exception as e:
        logger.warning(f"Could not get MCP tool descriptions: {e}")
        # Don't provide fallback descriptions - only show what's actually available
        return ""
    
    if not tool_descriptions:
        logger.info("No tool descriptions available from enabled servers")
        return "No tools currently available."
    
    return "\n".join(tool_descriptions)

def _get_tool_call_formats_from_mcp(available_tools: list) -> str:
    """Generate tool call format examples from actual MCP tool schemas."""
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        if not mcp_manager.is_initialized:
            return _get_fallback_tool_formats(available_tools)
            
        # Get all MCP tools with their schemas (already filters by enabled servers only)
        mcp_tools = mcp_manager.get_all_tools()
        tool_schemas = {tool.name: tool.inputSchema for tool in mcp_tools}
        
        format_sections = []
        
        for tool_name in available_tools:
            clean_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            display_name = f"mcp_{clean_name}" if not tool_name.startswith("mcp_") else tool_name
            
            # Only generate format examples for tools that are actually available from enabled servers
            schema = tool_schemas.get(clean_name)
            if clean_name in tool_schemas and schema and "properties" in schema:
                # Generate example arguments from schema
                example_args = _generate_example_args_from_schema(schema, clean_name)
                
                format_sections.append(f"""**{display_name} Format:**
```
""" + TOOL_SENTINEL_OPEN + """
<name>""" + display_name + """</name>
<arguments>
""" + example_args + """
</arguments>
""" + TOOL_SENTINEL_CLOSE + """
```""")
        
        if format_sections:
            return "\n\n".join(format_sections)
        else:
            # Don't use fallback formats - only show what's actually available
            logger.info("No tool format examples available from enabled servers")
            return "No tool formats currently available."
            
    except Exception as e:
        logger.warning(f"Could not get MCP tool schemas: {e}")
        # Don't provide fallback formats - only show what's actually available
        return ""
 
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
 
# Removed _get_fallback_tool_formats() function as it was hardcoding shell tool examples
# even when shell server was disabled. Now we only show formats for actually enabled tools.

def register_extensions(manager):
    """
    Register all extensions in this module with the extension manager.
    
    Args:
        manager: The PromptExtensionManager instance
    """
    # Extensions are registered via decorators
    pass
