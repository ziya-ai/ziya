"""
LLM Provider abstraction layer.

Provides a unified interface for streaming LLM responses from different backends
(Bedrock, Anthropic Direct, OpenAI, Google, OpenRouter, etc.) so that the
StreamingToolExecutor orchestrator can remain provider-agnostic.
"""

from .base import (
    LLMProvider,
    StreamEvent,
    TextDelta,
    ToolUseStart,
    ToolUseInput,
    ToolUseEnd,
    UsageEvent,
    ThinkingDelta,
    ErrorEvent,
    StreamEnd,
    ProviderConfig,
)

__all__ = [
    "LLMProvider",
    "StreamEvent",
    "TextDelta",
    "ToolUseStart",
    "ToolUseInput",
    "ToolUseEnd",
    "UsageEvent",
    "ThinkingDelta",
    "ErrorEvent",
    "StreamEnd",
    "ProviderConfig",
]
