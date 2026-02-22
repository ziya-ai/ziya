"""
OpenAI-specific prompt extensions.

This module contains prompt extensions for OpenAI models accessed
via the native OpenAI API endpoint (not Bedrock-hosted OpenAI models).
"""

import re
from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger


@prompt_extension(
    name="openai_gpt_family_extension",
    extension_type="family",
    target="openai-gpt",
    config={
        "enabled": True,
        "priority": 20,
    },
)
def openai_gpt_family_extension(prompt: str, context: dict) -> str:
    """
    Adjust prompt for OpenAI GPT family models.

    Removes XML-based tool instructions (OpenAI uses native function calling)
    and adds GPT-specific guidance.
    """
    if not context.get("config", {}).get("enabled", True):
        return prompt

    # Strip XML tool instructions that conflict with native function calling
    cleaned = re.sub(
        r"\n\n## MCP Tool Usage - CRITICAL INSTRUCTIONS.*",
        "",
        prompt,
        flags=re.DOTALL,
    )

    if len(cleaned) < len(prompt):
        logger.info("OPENAI_EXTENSION: Removed conflicting XML tool instructions")

    return cleaned
