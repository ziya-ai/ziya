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

def register_extensions(manager):
    """
    Register all extensions in this module with the extension manager.
    
    Args:
        manager: The PromptExtensionManager instance
    """
    # Extensions are registered via decorators, but we can add any manual registrations here
    pass
