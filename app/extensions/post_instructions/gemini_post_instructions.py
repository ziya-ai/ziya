"""
Gemini-specific post-instructions.

This module contains post-instructions for Google Gemini models.
"""

from app.utils.post_instructions import post_instruction
from app.utils.logging_utils import logger

@post_instruction(
    name="gemini_family_post_instruction",
    instruction_type="family",
    target="gemini",
    config={
        "enabled": True,
        "priority": 5
    }
)
def gemini_family_post_instruction(query: str, context: dict) -> str:
    """
    Add post-instructions for all Gemini family models.
    
    Args:
        query: The original user query
        context: Post-instruction context
        
    Returns:
        str: Modified query with post-instructions
    """
    if not context.get("config", {}).get("enabled", True):
        return query
    
    # Add Gemini family specific post-instructions
    gemini_post_instruction = """

IMPORTANT: Remember to use diff formatting in any response where it would be appropriate. If you do not have access to a necessary file, do not make up approximations, ask instead for the file to be added to your context.
"""
    
    # Append the post-instruction to the query
    return query + gemini_post_instruction

def register_post_instructions(manager):
    """
    Register all post-instructions in this module with the post-instruction manager.
    
    Args:
        manager: The PostInstructionManager instance
    """
    # Post-instructions are registered via decorators, but we can add any manual registrations here
    pass
