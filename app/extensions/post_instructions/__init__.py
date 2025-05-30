"""
Post-instructions package.

This package contains post-instructions for different models, families, and endpoints.
These instructions are added after user queries but hidden from the user.
"""

def init_post_instructions():
    """
    Initialize all post-instructions.
    
    This function imports all post-instruction modules to ensure they are registered.
    """
    # Import all post-instruction modules to register them
    from app.extensions.post_instructions import gemini_post_instructions
    from app.extensions.post_instructions import sonnet_post_instructions
    
    # Add more imports here as needed
