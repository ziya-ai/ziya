"""Base class for MCP tools."""
from abc import ABC, abstractmethod
from typing import Any


class BaseMCPTool(ABC):
    """Base class for all MCP tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass
    
    @property
    def is_internal(self) -> bool:
        """Whether tool output should be hidden from user (default: False)."""
        return False
    
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool."""
        pass
