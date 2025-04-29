"""
Global prompt extensions.

This module contains prompt extensions that apply to all models.
Note: These are only used for special cases that aren't covered in the baseline prompt.
"""

from app.utils.prompt_extensions import prompt_extension
from app.utils.logging_utils import logger

# No global extensions by default - the baseline prompt covers standard instructions

def register_extensions(manager):
    """
    Register all extensions in this module with the extension manager.
    
    Args:
        manager: The PromptExtensionManager instance
    """
    # Extensions are registered via decorators, but we can add any manual registrations here
    pass
