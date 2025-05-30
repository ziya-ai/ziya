"""
Gemini-specific prompt extensions.

This module contains prompt extensions for Google Gemini models.
"""

from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

@prompt_extension(
    name="gemini_family_extension",
    extension_type="family",
    target="gemini",
    config={
        "enabled": True,
        "priority": 5
    }
)
def gemini_family_extension(prompt: str, context: dict) -> str:
    """
    Add instructions for all Gemini family models.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        str: Modified prompt
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt
    
    # Add Gemini family specific instructions
    gemini_instructions = """
GEMINI FAMILY INSTRUCTIONS:
1. When generating code, focus on clarity and maintainability
2. For complex code explanations, use step-by-step breakdowns
3. When suggesting changes, provide clear reasoning for each modification
4. Leverage your multimodal capabilities when appropriate for code visualization
"""
    
    # Find a good place to insert the instructions
    if "CRITICAL: INSTRUCTION PRESERVATION:" in prompt:
        # Insert after the instruction preservation section
        parts = prompt.split("CRITICAL: INSTRUCTION PRESERVATION:", 1)
        preservation_section = parts[1].split("\n\n", 1)
        return parts[0] + "CRITICAL: INSTRUCTION PRESERVATION:" + preservation_section[0] + "\n\n" + gemini_instructions + "\n\n" + preservation_section[1]
    else:
        # Just add to the beginning
        return gemini_instructions + "\n\n" + prompt

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

def register_extensions(manager):
    """
    Register all extensions in this module with the extension manager.
    
    Args:
        manager: The PromptExtensionManager instance
    """
    # Extensions are registered via decorators, but we can add any manual registrations here
    pass
