"""
Sonnet-specific post-instructions.

This module contains post-instructions for Claude Sonnet models.
"""

from app.utils.post_instructions import post_instruction
from app.utils.logging_utils import logger

@post_instruction(
    name="sonnet37_model_post_instruction",
    instruction_type="model",
    target="sonnet3.7",
    config={
        "enabled": True,
        "priority": 10
    }
)
def sonnet37_model_post_instruction(query: str, context: dict) -> str:
    """
    Add post-instructions specifically for the sonnet3.7 model.
    
    Args:
        query: The original user query
        context: Post-instruction context
        
    Returns:
        str: Modified query with post-instructions
    """
    if not context.get("config", {}).get("enabled", True):
        return query
    
    # Add sonnet3.7 specific post-instruction for git diff format
    sonnet_post_instruction = """

IMPORTANT: All responses that involve changes to existing code or clean creation of new code files MUST be presented in git diff format (unless explicitly otherwise instructed, on a per-query basis)
You do not need to explicitly note in your text that this is the format being used.
"""
    
    # Append the post-instruction to the query
    return query + sonnet_post_instruction

def register_post_instructions(manager):
    """
    Register all post-instructions in this module with the post-instruction manager.
    
    Args:
        manager: The PostInstructionManager instance
    """
    # Post-instructions are registered via decorators, but we can add any manual registrations here
    pass
