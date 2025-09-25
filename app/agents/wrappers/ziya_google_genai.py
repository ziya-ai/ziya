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

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Override astream to handle tool code interception for gemini-1.5-flash."""
        
        chunk_count = 0
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            chunk_count += 1
            
            # Check if this is an incomplete tool code block (only for gemini-1.5-flash)
            if hasattr(chunk, 'message') and hasattr(chunk.message, 'content'):
                content = chunk.message.content
                
                # Check for tool_code pattern in any form (gemini-1.5-flash specific issue)
                if content and ('tool_code' in content):
                    logger.info("Intercepting tool code block, executing actual tools")
                    # Simple tool execution for pwd command
                    last_message = messages[-1].content if messages else ""
                    if "current working directory" in last_message.lower() or "pwd" in last_message.lower():
                        try:
                            import subprocess
                            result = subprocess.run(['pwd'], capture_output=True, text=True)
                            pwd_output = result.stdout.strip()
                            
                            response = f"I'll get the current working directory for you.\n\n```bash\n$ pwd\n{pwd_output}\n```\n\nThe current working directory is: `{pwd_output}`"
                            
                            new_chunk = ChatGenerationChunk(
                                message=AIMessageChunk(content=response)
                            )
                            yield new_chunk
                            return
                        except Exception as e:
                            logger.warning(f"Failed to execute pwd command: {e}")
            
            yield chunk
        
        logger.info(f"[ZiyaChatGoogleGenerativeAI WRAPPER] Exiting custom _astream after {chunk_count} chunks")
