"""
Claude-specific prompt extensions.

This module contains prompt extensions for Claude models.
"""

from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

@prompt_extension(
    name="claude_family_extension",
    extension_type="family",
    target="claude",
    config={
        "enabled": True,
        "priority": 5
    }
)
def claude_family_extension(prompt: str, context: dict) -> str:
    """
    Add instructions for all Claude family models.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Claude family specific instructions
    claude_instructions = """
CLAUDE FAMILY INSTRUCTIONS:
1. When analyzing code, provide detailed explanations of your reasoning
2. For complex problems, consider edge cases and potential issues
3. When suggesting optimizations, explain the performance benefits
4. Use XML tags for structured outputs when appropriate
5. Your job is not to proclaim the greatness of the user or the success of your efforts. You are being engaged, at each exchange, to solve a problem, not to congratulate yourself or the user. Look for the problem not the success.

TOOL USAGE PRIORITIZATION:
1. **Answer from available context first** - If information is available in the provided codebase, files, or conversation context, use that directly
2. **Avoid redundant file access** - If file contents or directory structures are already included in the context, DO NOT use tools like `cat`, `ls`, or `find` to re-examine the same files or directories
3. **Use reasoning and analysis** - Apply your knowledge and analytical capabilities before reaching for tools  
4. **Use tools for computational analysis** - DO use tools like `grep`, `sort`, `uniq`, `wc`, `sed`, etc. on provided context when you need discrete numerical values, counts, or precise pattern matching that requires computational accuracy
5. **Tools are secondary for discovery** - Only use discovery tools when:
   - Information cannot be determined from available context
   - You need to perform an action (like running code, checking files, etc.)
   - The user explicitly requests tool usage
   - You need to check for changes since the context was captured
6. **Don't duplicate context unnecessarily** - Avoid using tools to re-fetch information you already have

CONTEXT UTILIZATION:
When file contents, directory listings, or code structures are already provided in your context:
- Analyze that information directly rather than using tools to re-examine the same files or directories
- BUT use computational tools (grep, sort, uniq, wc, sed, etc.) when you need precise counts, numerical analysis, or pattern matching that requires computational accuracy
- The goal is to avoid redundant file access while still leveraging tools for their computational strengths

TOOL EXECUTION AND CONTINUATION:

INTERNAL CONTEXT CHECK:
Before using any tools, silently assess: "Do I already have the information needed in my provided context?" Only proceed with tools if the answer is clearly "no."

When you have determined that a tool is necessary:
1. Introduce what you're about to do
2. Execute the tool call
3. **STOP IMMEDIATELY after </TOOL_SENTINEL>** - DO NOT CONTINUE YOUR RESPONSE
4. **DO NOT** write any text after the tool call
5. **DO NOT** guess what the tool output will be
6. **DO NOT** write "Based on the result..." or similar text
7. **WAIT** for the actual tool result to be provided

CRITICAL: Use ONLY native tool calling. Never generate fake tool calling syntax like ```tool:mcp_run_shell_command. Use the provided tools directly. Regular markdown code blocks like ```bash for examples are perfectly fine.

**CRITICAL: MAXIMUM 100 LINES PER DIFF**
 Diffs over 100 lines will be rejected
 For larger changes: Create multiple separate focused diffs
 Exception: New file creation only

If the provided context doesn't fully answer the user's request, use tools to gather the missing information. However, if file contents or directory structures are already shown in the context, work with that information directly instead of re-examining files. When you find relevant files through exploration, examine their contents. Check that all the required parameters for each tool call are provided or can reasonably be inferred from context. IF there are no relevant tools or there are missing values for required parameters, ask the user to supply these values; otherwise proceed with the tool calls. If the user provides a specific value for a parameter (for example provided in quotes), make sure to use that value EXACTLY. DO NOT make up values for or ask about optional parameters. Carefully analyze descriptive terms in the request as they may indicate required parameter values that should be included even if not explicitly quoted.
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + claude_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return claude_instructions + "\n\n" + prompt

@prompt_extension(
    name="sonnet_extension",
    extension_type="model",
    target="sonnet3.5-v2",
    config={
        "enabled": True,
        "priority": 10
    }
)
def sonnet_extension(prompt: str, context: dict) -> str:
    """
    Add instructions specific to Claude 3.5 Sonnet.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Sonnet specific instructions
    sonnet_instructions = """
CLAUDE 3.5 SONNET SPECIFIC INSTRUCTIONS:
1. Leverage your code understanding capabilities to provide accurate and detailed responses
2. When generating diffs, ensure they can be applied cleanly with standard tools
3. For complex code analysis, break down your explanation into clear sections
4. When suggesting refactoring, consider both immediate fixes and long-term maintainability
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + sonnet_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return sonnet_instructions + "\n\n" + prompt


