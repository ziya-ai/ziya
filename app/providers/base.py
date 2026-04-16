"""
Base LLM Provider interface and normalized stream event types.

Every LLM backend (Bedrock, Anthropic Direct, OpenAI, Google, OpenRouter)
implements LLMProvider.  The StreamingToolExecutor consumes the normalized
StreamEvent stream and handles all orchestration (tool loop, retry
coordination, repetition detection, etc.) without caring which backend
produced the events.

Design principles:
  - Providers are thin: ~150-300 lines each.  They own API client init,
    request body building, stream parsing, and message formatting.
  - The orchestrator is thick: it owns every cross-cutting concern
    (throttle coordination, hallucination detection, feedback monitor, …).
  - Events are dataclasses, not dicts — cheap to create, easy to
    pattern-match with isinstance().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, AsyncGenerator, Dict, List, Optional


# ---------------------------------------------------------------------------
# Stream events — the normalized vocabulary between provider and orchestrator
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Base class for all stream events.  Use isinstance() to dispatch."""


@dataclass(frozen=True, slots=True)
class TextDelta(StreamEvent):
    """A chunk of assistant text output."""
    content: str


@dataclass(frozen=True, slots=True)
class ToolUseStart(StreamEvent):
    """A tool_use content block has started."""
    id: str
    name: str
    index: int = 0  # content block index, used by Bedrock for correlation


@dataclass(frozen=True, slots=True)
class ToolUseInput(StreamEvent):
    """Incremental JSON fragment for a tool call's input."""
    partial_json: str
    index: int = 0


@dataclass(frozen=True, slots=True)
class ToolUseEnd(StreamEvent):
    """A tool_use content block is complete with fully parsed input."""
    id: str
    name: str
    input: Dict[str, Any]
    index: int = 0


@dataclass(frozen=True, slots=True)
class UsageEvent(StreamEvent):
    """Token usage reported by the API (may arrive multiple times)."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ThinkingDelta(StreamEvent):
    """A chunk of extended-thinking / chain-of-thought content."""
    content: str


@dataclass(frozen=True, slots=True)
class ProcessingEvent(StreamEvent):
    """The model is still processing but has not emitted data recently.

    Providers emit this periodically during long silences (e.g. extended
    thinking on Bedrock where no thinking_delta events arrive).  The
    orchestrator forwards it to the frontend so the UI can show a
    'thinking' spinner instead of treating the silence as a stall.
    """
    elapsed_seconds: float = 0.0
    phase: str = "thinking"  # "thinking" | "processing" | "connecting"


class ErrorType(Enum):
    """Categorised error types so the orchestrator can react appropriately."""
    THROTTLE = auto()        # Rate limit / "too many tokens"
    CONTEXT_LIMIT = auto()   # Input too long
    READ_TIMEOUT = auto()    # Network timeout
    OVERLOADED = auto()      # 529 overloaded
    AUTH = auto()             # Permission / credential errors
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class ErrorEvent(StreamEvent):
    """An error from the API.  *retryable* hints whether the orchestrator
    should retry (the provider has already exhausted its own internal retries
    when it yields this)."""
    message: str
    error_type: ErrorType = ErrorType.UNKNOWN
    retryable: bool = False
    status_code: Optional[int] = None


@dataclass(frozen=True, slots=True)
class StreamEnd(StreamEvent):
    """The model finished generating for this turn."""
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens | stop_sequence


# ---------------------------------------------------------------------------
# Provider configuration — passed by the orchestrator on each call
# ---------------------------------------------------------------------------

@dataclass
class ThinkingConfig:
    """Thinking / reasoning mode configuration."""
    enabled: bool = False
    mode: str = "adaptive"          # "adaptive" | "enabled"
    effort: str = "high"            # "low" | "medium" | "high" | "xhigh" | "max"
    budget_tokens: int = 16000      # for mode="enabled"


@dataclass
class ProviderConfig:
    """Per-request configuration the orchestrator passes to the provider.

    The orchestrator owns the *values*; the provider translates them into
    the backend-specific request format.
    """
    max_output_tokens: int = 16384
    temperature: Optional[float] = 0.3
    thinking: Optional[ThinkingConfig] = None

    # Cache control
    enable_cache: bool = True

    # Extended context (provider will add appropriate headers/params)
    use_extended_context: bool = False

    # Tool suppression (orchestrator may suppress tools to break loops)
    suppress_tools: bool = False

    # Provider-specific model config passthrough (from ModelManager)
    model_config: Dict[str, Any] = field(default_factory=dict)

    # Iteration number — providers may use this for cache control strategy
    iteration: int = 0


# ---------------------------------------------------------------------------
# Abstract provider interface
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract base for LLM streaming providers.

    Each concrete provider (Bedrock, Anthropic, OpenAI, …) implements this
    interface.  The StreamingToolExecutor calls these methods and never
    touches provider-specific APIs directly.

    Lifecycle:
      1. ``__init__`` — create API client, resolve model ID
      2. ``stream_response`` — called once per orchestrator iteration
      3. ``build_assistant_message`` / ``build_tool_result_message`` —
         called by the orchestrator to append to conversation history
         in the format the provider expects
    """

    @abstractmethod
    async def stream_response(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Send a request and yield normalized ``StreamEvent`` objects.

        The provider is responsible for:
          - Building the backend-specific request body
          - Retry logic for transient errors (rate limits, timeouts)
          - Parsing the backend-specific stream into ``StreamEvent`` types

        If retries are exhausted, yield an ``ErrorEvent`` and return.
        Do NOT raise — the orchestrator handles errors via events.
        """
        yield  # type: ignore[misc]  # make this a generator for type checkers

    @abstractmethod
    def build_assistant_message(
        self,
        text: str,
        tool_uses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a conversation-history message for the assistant turn.

        ``tool_uses`` is a list of dicts with keys ``id``, ``name``, ``input``.
        The provider formats these into its native tool_use representation.
        """

    @abstractmethod
    def build_tool_result_message(
        self,
        tool_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a conversation-history message carrying tool results.

        ``tool_results`` is a list of dicts with keys ``tool_use_id``,
        ``content`` (str), and optionally ``is_error`` (bool).
        """

    def prepare_cache_control(
        self,
        messages: List[Dict[str, Any]],
        iteration: int,
    ) -> List[Dict[str, Any]]:
        """Apply provider-specific cache control markers to messages.

        Default implementation returns messages unchanged.  Providers that
        support prompt caching (Bedrock, Anthropic) override this.
        """
        return messages

    def supports_feature(self, feature_name: str) -> bool:
        """Query whether this provider supports a named capability.

        Known feature names:
          - ``thinking``            — extended thinking / chain of thought
          - ``adaptive_thinking``   — adaptive effort thinking
          - ``extended_context``    — larger-than-default context window
          - ``cache_control``       — prompt caching
          - ``assistant_prefill``   — conversation ending with assistant msg

        Default returns False.  Providers override to report their caps.
        """
        return False

    @property
    def provider_name(self) -> str:
        """Human-readable name for logging (e.g. 'bedrock', 'anthropic')."""
        return self.__class__.__name__
