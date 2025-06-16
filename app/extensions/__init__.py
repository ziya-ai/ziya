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
    from app.utils.logging_utils import logger
    
    logger.info("EXTENSIONS: init_extensions() called")
    
    # Get the extensions directory
    prompt_extensions_dir = Path(__file__).parent / "prompt_extensions"
    logger.info(f"EXTENSIONS: Loading extensions from {prompt_extensions_dir}")
    
    # Load prompt extensions
    PromptExtensionManager.load_extensions_from_directory(str(prompt_extensions_dir))
    logger.info("EXTENSIONS: Finished loading extensions from directory")
    
    # Load MCP-specific prompt extensions
    from app.extensions.prompt_extensions import mcp_prompt_extensions
    logger.info("EXTENSIONS: MCP prompt extensions imported")
    
    # Verify extensions were registered
    global_extensions = PromptExtensionManager._extensions["global"]
    logger.info(f"EXTENSIONS: Registered global extensions: {list(global_extensions.keys())}")
    if "mcp_usage_guidelines" in global_extensions:
        logger.info("EXTENSIONS: ✅ MCP guidelines extension successfully registered")
    else:
        logger.error("EXTENSIONS: ❌ MCP guidelines extension NOT registered")
    
    # Initialize post-instructions
    from app.extensions.post_instructions import init_post_instructions
    init_post_instructions()
    logger.info("EXTENSIONS: Post-instructions initialized")
