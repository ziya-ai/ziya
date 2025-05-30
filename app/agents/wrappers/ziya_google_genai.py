import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI # Import the base class

from app.utils.logging_utils import logger # Use your logger

class ZiyaChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """
    Custom wrapper for ChatGoogleGenerativeAI to add specific debugging
    and potential fixes for streaming behavior, like newline handling.
    """

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Override astream to add logging and potential fixes."""
        logger.debug("[ZiyaChatGoogleGenerativeAI WRAPPER] Entering custom _astream")
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            # --- Detailed Chunk Debugging ---
            chunk_type = type(chunk)            
            chunk_content = None
            content_repr = "N/A"
            chunk_repr = repr(chunk)

            if hasattr(chunk, 'text'):
                chunk_content = chunk.text
                content_repr = repr(chunk_content)
            elif hasattr(chunk, 'content'): # AIMessageChunk uses 'content'
                chunk_content = chunk.content
                content_repr = repr(chunk_content)

            # FIXME: line end detection on gemini consistently has issues with +add lines joining prior lines
            # I'm keeoing this debug stuff in here until we resolve it, but disabling for now.

            #logger.debug(f"[WRAPPER DEBUG] Chunk Type: {chunk_type}")
            #logger.debug(f"[WRAPPER DEBUG] Chunk Repr: {chunk_repr}")
            #logger.debug(f"[WRAPPER DEBUG] Extracted Content Repr: {content_repr}")
            # --- End Debugging ---

            # --- Potential Fix (Optional - Apply if logging confirms missing newline) ---
            # if isinstance(chunk_content, str) and not chunk_content.endswith('\n'):
            #     # If the chunk seems like it should end a line but doesn't
            #     # This is heuristic - might need refinement
            #     if chunk_content.strip(): # Avoid adding newline to empty/whitespace chunks
            #         logger.debug("[WRAPPER FIX] Appending newline to chunk content.")
            #         # Modify the chunk content before yielding
            #         # Note: Modifying LangChain objects directly can be tricky.
            #         # It might be better to create a new chunk if modification is needed.
            #         # For AIMessageChunk:
            #         if isinstance(chunk, AIMessageChunk):
            #              chunk = AIMessageChunk(content=chunk_content + '\n', id=chunk.id, response_metadata=chunk.response_metadata)
            #         # For ChatGenerationChunk (if that's what Gemini yields):
            #         elif isinstance(chunk, ChatGenerationChunk):
            #              new_message = AIMessageChunk(content=chunk_content + '\n')
            #              chunk = ChatGenerationChunk(message=new_message, generation_info=chunk.generation_info)
            #         else:
            #              # Fallback or handle other types if necessary
            #              logger.warning(f"[WRAPPER FIX] Could not append newline to chunk type: {chunk_type}")
            # --- End Potential Fix ---

            yield chunk
        logger.debug("[ZiyaChatGoogleGenerativeAI WRAPPER] Exiting custom _astream")

    # You might need to override _stream as well if non-async streaming is used    # def _stream( ... ) -> Iterator[ChatGenerationChunk]:
    #    # Similar logic as _astream
    #    pass

    # You might also need to override invoke/ainvoke if fixes are needed there,
    # but streaming is the primary focus for the newline issue.
