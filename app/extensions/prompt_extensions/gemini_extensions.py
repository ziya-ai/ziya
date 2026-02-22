"""
Gemini-specific prompt extensions.

This module contains prompt extensions for Google Gemini models.
"""

from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

@prompt_extension(
    name="gemini_pro_family_extension",
    extension_type="family",
    target="gemini-pro",
    config={
        "enabled": True,
        "priority": 20
    }
)
def gemini_pro_family_extension(prompt: str, context: dict) -> str:
    """
    Add instructions for Gemini Pro family models.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    import re
    from app.utils.logging_utils import logger
    logger.info(f"GEMINI_EXTENSION: Called with prompt length: {len(prompt)}")
    logger.info(f"GEMINI_EXTENSION: Context: {context}")
    
    if not context.get("config", {}).get("enabled", True):
        logger.info("GEMINI_EXTENSION: Extension disabled, returning original prompt")
        return prompt

    # Aggressively remove any XML-based tool instructions to prevent conflicts with native function calling.
    # This is a safeguard against other extensions incorrectly adding these instructions.
    cleaned_prompt = re.sub(r'\n\n## MCP Tool Usage - CRITICAL INSTRUCTIONS.*', '', prompt, flags=re.DOTALL)
    
    if len(cleaned_prompt) < len(prompt):
        logger.info("GEMINI_EXTENSION: Removed conflicting XML tool instructions from the prompt.")
    else:
        logger.info("GEMINI_EXTENSION: No conflicting XML tool instructions found to remove.")

    return cleaned_prompt

@prompt_extension(
name="gemini_flash_family_extension",
    extension_type="family",
    target="gemini-flash",
    config={
        "enabled": True,
        "priority": 5
    }
)
def gemini_flash_family_extension(prompt: str, context: dict) -> str:
    """
    Add instructions for Gemini Flash family models.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Replace TOOL_SENTINEL format with Google-compatible format
    if "<TOOL_SENTINEL>" in prompt:
        # Replace the tool format examples with Google-compatible format
        prompt = prompt.replace(
            "**mcp_get_current_time Format:**\n```\n<TOOL_SENTINEL>\n<name>mcp_get_current_time</name>\n<arguments>{\n  \"format\": \"readable\"\n}</arguments>\n</TOOL_SENTINEL>\n```",
            "**mcp_get_current_time Format:**\n```\nI'll use the get_current_time tool to get the current date and time.\n```"
        )
        prompt = prompt.replace(
            "**mcp_run_shell_command Format:**\n```\n<TOOL_SENTINEL>\n<name>mcp_run_shell_command</name>\n<arguments>{\n  \"command\": \"ls -la\",\n  \"timeout\": \"1\"\n}</arguments>\n</TOOL_SENTINEL>\n```",
            "**mcp_run_shell_command Format:**\n```\nI'll use the shell command tool to execute: pwd\n```"
        )
        
        # Add Google-specific tool instructions
        gemini_tool_instructions = """
GEMINI TOOL USAGE:
- Use tools when requested by the user
- For shell commands, use the mcp_run_shell_command tool with the appropriate command
- For time queries, use the mcp_get_current_time tool
- Provide actual tool results rather than describing what you would do
"""
        
        # Insert the tool instructions
        if "**Usage Rules:**" in prompt:
            prompt = prompt.replace("**Usage Rules:**", gemini_tool_instructions + "\n\n**Usage Rules:**")
    
    return prompt

@prompt_extension(
    name="gemini_pro_extension",
    extension_type="model",
    target="gemini-1.5-pro",
    config={
        "enabled": True,
        "priority": 10
    }
)
def gemini_pro_extension(prompt: str, context: dict) -> str:
    """
    Add instructions specific to Gemini 1.5 Pro.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Gemini Pro specific instructions
    gemini_pro_instructions = """
GEMINI 1.5 PRO SPECIFIC INSTRUCTIONS:
1. Use your large context window effectively to analyze entire codebases
2. When generating diffs, ensure they include complete file paths
3. For complex refactoring tasks, consider the entire codebase structure
4. Leverage your code understanding capabilities for accurate suggestions
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + gemini_pro_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return gemini_pro_instructions + "\n\n" + prompt

@prompt_extension(
    name="gemini_flash_extension",
    extension_type="model",
    target="gemini-1.5-flash",
    config={
        "enabled": True,
        "priority": 10
    }
)
def gemini_flash_extension(prompt: str, context: dict) -> str:
    """
    Add instructions specific to Gemini 1.5 Flash.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Gemini Flash specific instructions
    gemini_flash_instructions = """
GEMINI 1.5 FLASH SPECIFIC INSTRUCTIONS:
1. Prioritize concise and efficient responses
2. For code suggestions, focus on the most important changes
3. When explaining code, be direct and to the point
4. Use your speed advantage to provide quick solutions
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + gemini_flash_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return gemini_flash_instructions + "\n\n" + prompt


def _get_gemini_strategy_text():
    """Shared strategy text for all Gemini models."""
    return """
GEMINI ADVANCED AGENT STRATEGY:

### 🧠 ENGINEERING EXCELLENCE (OPUS-LEVEL STANDARDS)
1. **Deep Reasoning**: Do not just provide the solution. Explain *why* it is the best approach. Discuss trade-offs (performance vs. readability, complexity vs. maintainability).
2. **Edge Cases**: Actively hunt for edge cases. Ask yourself: "What if this input is empty?", "What if the network fails?", "What about concurrent access?". Address these in your code.
3. **Security First**: Treat all inputs as untrusted. Sanitize paths, validate arguments, and avoid shell injection risks.
4. **Code Quality**: Write production-grade code. Use meaningful variable names, add docstrings, and follow PEP8 (or language equivalent).
5. **Self-Correction**: If you notice a mistake in your reasoning *while* generating, correct it immediately. Don't double down on errors.

### 🛠️ TOOL & CONTEXT MASTERY

1. **Context Utilization**: You have a large context window. Always check if the information you need (file contents, directory structures) is already present in your context before using tools.
   - **STOP**: Do not use `read_file` or `ls` on paths already visible in your context.
   - **GO**: Use tools to explore *new* paths or run computations (tests, grep).

2. **Tool Usage Discipline**:
   - **Native Tools**: You have native tool calling capabilities. Use them precisely.
   - **No Simulation**: Never describe a tool call (e.g., "I will run...") without actually executing it.
   - **One Step at a Time**: For complex tasks, execute a tool, wait for the result, then proceed. Don't chain blind assumptions.

3. **Code Modification**:
   - **Diff Format**: Use standard git diffs for all code changes.
   - **Precision**: Ensure context lines in diffs match the file *exactly* (whitespace, comments).
   - **Verification**: If you are unsure of the file state, read it first. Don't guess.

4. **Reasoning**:
   - Explain your plan before executing destructive actions.
   - If a tool fails, analyze the error message and try a different approach (e.g., `find` instead of `ls`).
"""

@prompt_extension(
    name="gemini_3_strategy",
    extension_type="family",
    target="gemini-3",
    config={"enabled": True, "priority": 15}
)
def gemini_3_strategy(prompt: str, context: dict) -> str:
    """Apply advanced agent strategy to Gemini 3 family."""
    if not context.get("config", {}).get("enabled", True):
        return prompt
    return prompt + _get_gemini_strategy_text()

@prompt_extension(
    name="gemini_pro_strategy",
    extension_type="family",
    target="gemini-pro",
    config={"enabled": True, "priority": 15}
)
def gemini_pro_strategy(prompt: str, context: dict) -> str:
    """Apply advanced agent strategy to Gemini Pro family."""
    if not context.get("config", {}).get("enabled", True):
        return prompt
    return prompt + _get_gemini_strategy_text()

