"""
Custom message classes for handling different model response formats.
"""
from typing import Optional, Any, Dict, List, Union, Callable
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import Generation

class ZiyaString(str):
    """A string subclass that can hold additional attributes."""
    
    def __new__(cls, content, **kwargs):
        instance = super().__new__(cls, content)
        for key, value in kwargs.items():
            setattr(instance, key, value)
        return instance

class ZiyaMessageChunk(AIMessageChunk):
    """
    Custom message chunk class that handles different model formats.
    This ensures compatibility with both Anthropic and Nova models.
    """
    
    def __init__(
        self, 
        content: Union[str, List[Dict[str, str]], Dict[str, Any]], 
        id: Optional[str] = None,
        **kwargs
    ):
        # Process content based on its type
        if isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
            # Handle Nova format: list of dicts with text field
            text_content = ""
            for item in content:
                if isinstance(item, dict) and 'text' in item:
                    text_content += item['text']
            processed_content = text_content
        elif isinstance(content, dict) and 'text' in content:
            # Handle dict with text field
            processed_content = content['text']
        else:
            # Use as is
            processed_content = content
            
        # Initialize parent class
        super().__init__(content=processed_content, **kwargs)
        
        # Store original content and ID
        self.original_content = content
        self.id = id or f"ziya-{hash(str(processed_content)) % 10000}"
        self.message = processed_content
        
    def to_generation(self) -> Generation:
        """Convert to a Generation object for compatibility."""
        return Generation(
            text=str(self.content),
            generation_info={"id": self.id}
        )
