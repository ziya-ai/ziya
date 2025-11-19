import logging
import os
import re
import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessageChunk, AIMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult, ChatGeneration
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI # Import the base class

from app.utils.logging_utils import logger # Use your logger

class ZiyaChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """
    Custom wrapper for ChatGoogleGenerativeAI to add specific debugging
    and Google function calling support.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._google_function_caller = None
        
    def _get_google_function_caller(self):
        """Lazy initialize Google function caller."""
        if self._google_function_caller is None:
            from app.agents.wrappers.google_function_calling import GoogleFunctionCaller
            google_api_key = os.environ.get("GOOGLE_API_KEY")
            self._google_function_caller = GoogleFunctionCaller(self.model, google_api_key)
        return self._google_function_caller
    
    def bind_tools(self, tools: List[BaseTool], **kwargs) -> "ZiyaChatGoogleGenerativeAI":
        """Bind tools for Google function calling."""
        # Store tools for later use
        bound_model = self.__class__(**self.__dict__)
        bound_model._bound_tools = tools
        return bound_model
    
    async def ainvoke(self, input_messages: List[BaseMessage], **kwargs) -> AIMessage:
        """Override ainvoke to support Google function calling."""
        # Check if we have bound tools
        if hasattr(self, '_bound_tools') and self._bound_tools:
            try:
                function_caller = self._get_google_function_caller()
                result = await function_caller.call_with_tools(input_messages, self._bound_tools)
                return AIMessage(content=result)
            except Exception as e:
                logger.warning(f"Google function calling failed, falling back to regular chat: {e}")
                # Fall back to regular chat without tools
                return await super().ainvoke(input_messages, **kwargs)
        else:
            # No tools, use regular chat
            return await super().ainvoke(input_messages, **kwargs)
    
    def invoke(self, input_messages: List[BaseMessage], **kwargs) -> AIMessage:
        """Sync version of invoke with function calling support."""
        import asyncio
        
        # Check if we have bound tools
        if hasattr(self, '_bound_tools') and self._bound_tools:
            try:
                # Run async function calling in sync context
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    function_caller = self._get_google_function_caller()
                    result = loop.run_until_complete(
                        function_caller.call_with_tools(input_messages, self._bound_tools)
                    )
                    return AIMessage(content=result)
                finally:
                    loop.close()
            except Exception as e:
                logger.warning(f"Google function calling failed, falling back to regular chat: {e}")
                # Fall back to regular chat without tools
                return super().invoke(input_messages, **kwargs)
        else:
            # No tools, use regular chat
            return super().invoke(input_messages, **kwargs)
