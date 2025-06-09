"""
Extensions package for Ziya.

This package contains prompt extensions, post-instructions, and other extension mechanisms.
"""

from app.utils.prompt_extensions import PromptExtensionManager, prompt_extension
from app.utils.post_instructions import PostInstructionManager, post_instruction

# Initialize extensions
def init_extensions():
    """Initialize all extensions."""
    # Load prompt extensions from the extensions directory
    import os
    from pathlib import Path
    
    # Get the extensions directory
    prompt_extensions_dir = Path(__file__).parent / "prompt_extensions"
    
    # Load prompt extensions
    PromptExtensionManager.load_extensions_from_directory(str(prompt_extensions_dir))
    
    # Load MCP-specific prompt extensions
    from app.extensions.prompt_extensions import mcp_prompt_extensions
    
    # Initialize post-instructions
    from app.extensions.post_instructions import init_post_instructions
    init_post_instructions()
