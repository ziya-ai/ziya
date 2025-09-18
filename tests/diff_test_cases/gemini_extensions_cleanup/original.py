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

    # Add a concise instruction for Gemini models instead of replacing the entire prompt.
    # This ensures Gemini gets all critical instructions from the main prompt.
    gemini_instructions = """
GEMINI-SPECIFIC INSTRUCTIONS:
1.  Provide answers in a clear, direct, and helpful manner.
2.  When generating code changes, strictly adhere to the git diff format specified in the instructions.
3.  For tool usage, generate only the function call and wait for the result.
"""
    
    logger.info(f"GEMINI_EXTENSION: Appending Gemini-specific instructions.")
    return cleaned_prompt + gemini_instructions
