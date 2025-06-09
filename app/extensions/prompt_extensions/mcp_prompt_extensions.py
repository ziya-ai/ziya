"""
MCP-specific prompt extensions.

This module contains prompt extensions that provide guidance on how to interact
with MCP (Model Context Protocol) servers and tools.
"""

from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

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
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Check if MCP tools are available in the context
    # This would be passed from the agent system when MCP is initialized
    mcp_tools_available = context.get("mcp_tools_available", False)
    available_tools = context.get("available_mcp_tools", [])
    
    if not mcp_tools_available or not available_tools:
        logger.info("MCP_GUIDELINES: No MCP tools available or list is empty, returning original prompt.") # ADD THIS
        return prompt
    
    mcp_guidelines = """

**CRITICAL MCP TOOL VERIFICATION:**
Before using any MCP tool, verify the exact tool names available. The tools are prefixed with "mcp_" in the agent system.

**ACTUAL AVAILABLE TOOLS:** """ + str(available_tools) + """

## MCP Tool Usage Guidelines - CRITICAL INSTRUCTIONS

You have access to MCP (Model Context Protocol) tools that provide additional capabilities:

**AVAILABLE TOOL NAMES (use these exact names):**
### Available MCP Tools:
""" + "\n".join([f"- **{tool}**: Use for {_get_tool_description(tool)}" for tool in available_tools]) + """


**TOOL CALLING FORMAT - USE EXACTLY THIS SYNTAX:**

For shell commands, use EXACTLY this format:
```
<tool_call>
<name>mcp_run_shell_command</name>
<arguments>
{"command": "your_command_here"}
</arguments>
</tool_call>
```

For time queries, use EXACTLY this format:
```
<tool_call>
<name>mcp_get_current_time</name>
<arguments>
{"format": "readable"}
</arguments>
</tool_call>
```

### MCP Usage Best Practices:

**CRITICAL: You MUST actually execute tools when requested, not describe what you would do. When a user asks you to run a command or check something, USE THE TOOLS.**

1. **Shell Commands - EXECUTE THESE WHEN REQUESTED**: When using shell/command execution tools (mcp_run_shell_command):
   - Always explain what command you're running and why
   - Use safe, read-only commands when possible (ls, cat, grep, etc.)
   - Be cautious with write operations and always confirm intent
   - Respect the allowed command whitelist
   - **Format shell output as interactive session**: Present the output as if it were executed in a terminal:
     ```
     $ command_here
     [actual stdout/stderr output]
     ```
   - **DO NOT FABRICATE COMMAND OUTPUT - Always use the actual tool results**
   - mcp_run_shell_command (NOT run_shell_command)

2. **Time/Date Tools - USE mcp_get_current_time**: When checking time:
   - Use for scheduling, logging, or time-sensitive operations
   - Consider timezone context when relevant
   - **Always use the tool rather than guessing the current time**
   - mcp_get_current_time (NOT get_current_time)

3. **General MCP Tool Usage**:
   - **MANDATORY: Use MCP tools instead of making assumptions about system state**
   - Use tools to verify information rather than guessing
   - Combine multiple tools when needed for comprehensive analysis
   - Always handle tool errors gracefully and explain what went wrong
   - **If a tool call fails, show the actual error and try alternative approaches**
   - **Never simulate or fabricate tool responses - always wait for and use actual results**

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
        "mcp_get_resource": "accessing MCP resources and content",
        "get_current_time": "checking current system time and date (legacy name)",
        "run_shell_command": "executing safe shell commands to inspect system state (legacy name)"
    }
    return descriptions.get(tool_name, "specialized system operations")

def register_extensions(manager):
    """
    Register all extensions in this module with the extension manager.
    
    Args:
        manager: The PromptExtensionManager instance
    """
    # Extensions are registered via decorators
    pass
