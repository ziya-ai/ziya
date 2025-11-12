"""
Nova-specific prompt extensions.

This module contains prompt extensions for Nova models.
"""

from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

@prompt_extension(
    name="nova_lite_full_filepaths",
    extension_type="model",
    target="nova-lite",
    config={
        "enabled": True,
        "priority": 10
    }
)
def nova_lite_full_filepaths(prompt: str, context: dict) -> str:
    """
    Add instructions for Nova-Lite to include full filepaths in diffs.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Nova-Lite specific instructions
    nova_lite_instructions = """
IMPORTANT NOVA-LITE SPECIFIC INSTRUCTIONS:
1. Always include full filepaths in your diffs
2. When demonstrating code changes, prefer to answer with diffs when possible
3. Make sure each diff block starts with the complete filepath
4. Include enough context in each diff to clearly identify the location of changes
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + nova_lite_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return nova_lite_instructions + "\n\n" + prompt

@prompt_extension(
    name="nova_pro_thinking",
    extension_type="model",
    target="nova-pro",
    config={
        "enabled": True,
        "priority": 10
    }
)
def nova_pro_thinking(prompt: str, context: dict) -> str:
    """
    Add instructions for Nova-Pro to use thinking mode effectively.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Nova-Pro specific instructions for thinking mode
    nova_pro_instructions = """
NOVA-PRO THINKING MODE INSTRUCTIONS:
When using thinking mode:
1. Use <thinking> tags to work through complex problems step by step
2. Break down the problem into smaller parts
3. Consider multiple approaches before deciding on a solution
4. Show your reasoning process clearly
5. End your thinking with a clear conclusion
</thinking>

TOOL USAGE INSTRUCTIONS:
When tools are available, use them to provide accurate, real-time information:
- Use tools when the user asks for current information (directory contents, system status, etc.)
- Use tools when you need to execute commands or get live data
- Always prefer tool results over assumptions or outdated information
- IMPORTANT: When asked to count files, directories, or items, use the run_shell_command tool with appropriate commands (wc -l, grep -c, ls | wc -l, etc.) rather than trying to count manually - you are not good at counting and should rely on tools for accuracy

Your final answer should be concise and focused on the solution.
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + nova_pro_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return nova_pro_instructions + "\n\n" + prompt

@prompt_extension(
    name="nova_family_extension",
    extension_type="family",
    target="nova",
    config={
        "enabled": True,
        "priority": 5
    }
)
def nova_family_extension(prompt: str, context: dict) -> str:
    """
    Add instructions for all Nova family models.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Nova family specific instructions
    nova_instructions = """
NOVA FAMILY INSTRUCTIONS:
1. When generating code, focus on correctness and readability
2. Always include proper error handling in code examples
3. When suggesting changes, prefer minimal modifications that solve the problem
4. For complex code changes, explain your reasoning clearly
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + nova_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return nova_instructions + "\n\n" + prompt


