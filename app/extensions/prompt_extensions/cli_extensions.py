"""
CLI-specific prompt extensions.

These extensions add CLI-optimized instructions when running in chat mode.
"""

import os
from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

@prompt_extension(
    name="cli_mode_optimizations",
    extension_type="global",
    config={
        "enabled": True,
        "priority": 20  # Higher priority than MCP guidelines
    }
)
def cli_mode_optimizations(prompt: str, context: dict) -> str:
    """
    Add CLI-specific optimizations to the prompt.
    
    Args:
        prompt: The original prompt
        context: Extension context
        
    Returns:
        The prompt with CLI optimizations added
    """
    # Only apply in CLI mode
    mode = os.environ.get("ZIYA_MODE", "server")
    if mode != "chat":
        return prompt
    
    logger.debug("Applying CLI mode optimizations to prompt")
    
    cli_instructions = """

## CLI MODE OPTIMIZATIONS

You are running in CLI mode. Optimize your responses for terminal output:

**Response Style:**
- Be concise but complete - CLI users prefer direct answers
- Use markdown formatting (it will be rendered in the terminal)
- Avoid excessive verbosity or preamble

**Code Changes and Diffs:**
When providing code changes, follow these CLI-specific guidelines:

1. **ONE DIFF AT A TIME**: Present only ONE diff per response
   - After presenting a diff, STOP and wait for user feedback
   - Don't provide multiple diffs in a single response
   - The user will be prompted to apply/skip each diff interactively

2. **Diff Format**: Use standard git diff format in markdown:
   \`\`\`diff
   diff --git a/file.py b/file.py
   --- a/file.py
   +++ b/file.py
   @@ -10,7 +10,7 @@
    context line
   -old line
   +new line
    context line
   \`\`\`

3. **After presenting a diff**:
   - Add a brief explanation of what it changes
   - The CLI will automatically prompt the user to apply/skip
   - Wait for the user's decision before continuing

"""
    
    return prompt + cli_instructions
