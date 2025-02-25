from typing import List, Tuple

from pydantic import BaseModel, Field


class AgentInput(BaseModel):
    question: str
    config: dict = Field({})
    chat_history: List[Tuple[str, str]] = Field(..., extra={"widget": {"type": "chat"}})

DEFAULT_PORT = 6969

"""
Model References: 
- https://ai.google.dev/gemini-api/docs/models/gemini
"""

MODEL_MAPPING = {
    "sonnet3.7": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    "sonnet3.5": "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
    "sonnet3.5-v2": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "opus": "us.anthropic.claude-3-opus-20240229-v1:0",
    "sonnet": "us.anthropic.claude-3-sonnet-20240229-v1:0",
    "haiku": "us.anthropic.claude-3-haiku-20240307-v1:0",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-2.0-pro": "gemini-2.0-pro-exp-02-05",
    "gemini-2.0-think": "gemini-2.0-flash-thinking-exp-01-21",
    "gemini-1.5-pro": "gemini-1.5-pro",
    "gemini-1.5-flash": "gemini-1.5-flash",
}

MODEL_CHOICES = list(MODEL_MAPPING.keys())
SAMPLE_QUESTION = "How are you ?"
GEMINI_PREFIX = "gemini"
GOOGLE_API_KEY = "GOOGLE_API_KEY"
