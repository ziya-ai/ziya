"""
Base wrapper class for model implementations.
Defines the common interface that all model wrappers must implement.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Iterator, AsyncIterator

class BaseModelWrapper(ABC):
    """
    Base class for all model wrappers.
    Defines the common interface that all wrappers must implement.
    """

    def __init__(self, model_id: str, **kwargs):
        self.model_id = model_id
        self.params = kwargs

    @abstractmethod
    def invoke(self, messages: List[Dict[str, Any]], system: Optional[str] = None, **kwargs) -> str:
        """
        Invoke the model with the given messages and system prompt.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            system: Optional system prompt
            **kwargs: Additional parameters for the model

        Returns:
            The model's response as a string
        """
        pass

    @abstractmethod
    async def ainvoke(self, messages: List[Dict[str, Any]], system: Optional[str] = None, **kwargs) -> str:
        """
        Asynchronously invoke the model with the given messages and system prompt.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            system: Optional system prompt
            **kwargs: Additional parameters for the model

        Returns:
            The model's response as a string
        """
        pass

    @abstractmethod
    def stream(self, messages: List[Dict[str, Any]], system: Optional[str] = None, **kwargs) -> Iterator[str]:
        """
        Stream the model's response with the given messages and system prompt.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            system: Optional system prompt
            **kwargs: Additional parameters for the model

        Returns:
            An iterator of response chunks
        """
        pass

    @abstractmethod
    async def astream(self, messages: List[Dict[str, Any]], system: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        """
        Asynchronously stream the model's response with the given messages and system prompt.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
            system: Optional system prompt
            **kwargs: Additional parameters for the model

        Returns:
            An async iterator of response chunks
        """
        pass
