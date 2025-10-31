"""
Base class for MCP tools.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pydantic import BaseModel


class BaseMCPTool(ABC):
    """Base class for all MCP tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description."""
        pass
    
    @property
    def input_schema(self) -> Dict[str, Any]:
        """Get the input schema for this tool."""
        if hasattr(self, 'InputSchema'):
            return self.InputSchema.model_json_schema()
        return {"type": "object", "properties": {}}
    
    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool with the given parameters."""
        pass
    
    def __call__(self, **kwargs) -> Dict[str, Any]:
        """Make the tool callable."""
        import asyncio
        
        # If we're already in an async context, run directly
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, but need to handle the call
            # Create a task for the async execution
            return asyncio.create_task(self.execute(**kwargs))
        except RuntimeError:
            # No running loop, create one
            return asyncio.run(self.execute(**kwargs))
