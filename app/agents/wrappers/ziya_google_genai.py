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
     
     pass
