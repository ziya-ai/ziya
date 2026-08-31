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
    # Content-block index from the original response.  Set by providers
    # that support thinking passback so the assistant turn can be rebuilt
    # in original block order; other providers may leave the default.
    index: int = 0


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
    # Reasoning tokens billed WITHIN output_tokens (not additional to it).
    # Providers that do not report a breakdown leave this 0, which is
    # indistinguishable from "no thinking" -- deliberately, since neither
    # case has a figure to display.
    thinking_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ThinkingDelta(StreamEvent):
    """A chunk of extended-thinking / chain-of-thought content."""
    content: str


@dataclass(frozen=True, slots=True)
class ThinkingBlock(StreamEvent):
    """A COMPLETED thinking block, emitted at content_block_stop.

    Distinct from ``ThinkingDelta``, which is display-bound and carries no
    signature.  This carries the whole block plus the cryptographic
    ``signature`` the API requires when the block is echoed back, so reasoning
    state can survive a tool round-trip instead of being re-derived from the
    tool_result alone on every iteration.

    ``block_type`` is "thinking" or "redacted_thinking"; the latter carries
    opaque bytes in ``data`` and has no readable content.
    """
    content: str = ""
    signature: Optional[str] = None
    block_type: str = "thinking"
    data: Optional[str] = None
    index: int = 0


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
    SERVER_ERROR = auto()    # 5xx InternalServerException — persistent, not retried
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

    # Arbitrary provider-request passthrough (OpenAI-compatible family seam).
    # Providers that support it merge this into the request via the SDK's
    # extra_body escape hatch, so vendor-specific request params (e.g.
    # z.ai/vLLM reasoning fields) flow as DATA without per-vendor provider
    # code.  Empty by default — no effect unless a caller populates it.
    extra_body: Dict[str, Any] = field(default_factory=dict)


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
        thinking_blocks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Build a conversation-history message for the assistant turn.

        ``tool_uses`` is a list of dicts with keys ``id``, ``name``, ``input``.
        The provider formats these into its native tool_use representation.

        ``thinking_blocks``, when supplied, holds completed thinking blocks
        (already in the provider's native shape) that must be emitted BEFORE
        any text or tool_use block.  Only providers reporting
        ``supports_feature("thinking_passback")`` are ever given this; the
        orchestrator omits the argument entirely otherwise, so implementations
        that predate it keep working unchanged.
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

    # Content-block types a cache marker must never be attached to.
    #
    # The Anthropic API treats a signed reasoning block as immutable:
    # adding ANY key to it, cache_control included, fails validation with
    # "thinking or redacted_thinking blocks in the latest assistant
    # message cannot be modified", and the entire request 400s.  Observed
    # on a fable5 tool loop as messages.19.content.1, which killed the
    # turn outright rather than merely costing a cache hit.
    _CACHE_INELIGIBLE_BLOCK_TYPES = ("thinking", "redacted_thinking")

    @classmethod
    def _stamp_cache_control(cls, content_blocks: List[Dict[str, Any]]) -> bool:
        """Mark the last CACHEABLE block of content_blocks, in place.

        Returns True when a marker is present on exit, False when the
        message offers no legal target because every block is a reasoning
        block.  Walking backwards instead of taking [-1] is the whole
        point: an assistant turn ENDS with a thinking block whenever it
        carries neither text nor tool_use, which is exactly what
        build_assistant_message emits for empty text and an empty
        tool_uses list.

        Placing the marker on an earlier block is still useful, since it
        just ends the cached prefix before the trailing reasoning blocks.
        Skipping the message when no legal target exists is deliberate:
        that forfeits one breakpoint, a recoverable cache miss, whereas
        stamping the block forfeits the request.
        """
        for block in reversed(content_blocks):
            if not isinstance(block, dict):
                continue
            if block.get("type") in cls._CACHE_INELIGIBLE_BLOCK_TYPES:
                continue
            if "cache_control" not in block:
                block["cache_control"] = {"type": "ephemeral"}
            return True
        return False

    @staticmethod
    def _ordered_assistant_content(
        text: str,
        tool_uses: List[Dict[str, Any]],
        thinking_blocks: Optional[List[Dict[str, Any]]] = None,
        text_index: Optional[int] = None,
        text_segments: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Assemble assistant content blocks, preserving original order.

        The API requires thinking/redacted_thinking blocks in the latest
        assistant message to remain EXACTLY as they were in the original
        response — including their position relative to text and tool_use
        blocks.  With adaptive/interleaved thinking the model emits
        thinking blocks BETWEEN tool_use blocks, so coalescing them to the
        front is itself a modification and 400s the request:
          "thinking or redacted_thinking blocks in the latest assistant
           message cannot be modified. These blocks must remain as they
           were in the original response."

        Ordering metadata (all optional, supplied by the orchestrator):
          - each thinking block may carry ``_index`` — its content-block
            index in the original response, stripped before send
          - each tool_use dict may carry ``index`` (same index space)
          - ``text_index`` is the index of the first text block
          - ``text_segments`` — optional list of ``(index, text)`` pairs,
            one per ORIGINAL text content block.  Supplied when the model
            emitted multiple text blocks (adaptive thinking interleaves
            reasoning between prose segments): merging multi-block text
            into one block shifts every later thinking block out of its
            original position, which the API rejects as a modification.
            When present these replace the single merged ``text`` block.

        Blocks are emitted in original index order when every thinking
        block carries an index.  Without that metadata (legacy callers,
        synthetic turns) the historical order is kept — thinking, text,
        tool_use — which is also correct for classic non-interleaved
        thinking, where the single thinking block leads the turn.
        """
        # All-or-nothing signature guard: the API rejects a readable
        # ``thinking`` block whose signature is missing or empty — and
        # sending a PARTIAL set (just omitting the unsigned block) leaves
        # a gap that shifts every later block out of its original
        # position, which is the same "cannot be modified" 400 this
        # assembler exists to prevent.  Echoing NO thinking blocks is the
        # known-good shape (identical to ZIYA_DISABLE_THINKING_PASSBACK),
        # so degrade to that, loudly.
        if thinking_blocks:
            _unsigned = [
                tb for tb in thinking_blocks
                if tb.get("type") == "thinking" and not tb.get("signature")
            ]
            if _unsigned:
                from app.utils.logging_utils import logger
                logger.warning(
                    "🧠 THINKING_PASSBACK: %d of %d thinking block(s) "
                    "unsigned — echoing none for this turn (a partial "
                    "set shifts later blocks out of their original "
                    "positions and the API rejects the request)",
                    len(_unsigned), len(thinking_blocks),
                )
                thinking_blocks = None
        thinking_items: List[Any] = []
        for tb in (thinking_blocks or []):
            tb = dict(tb)
            idx = tb.pop("_index", None)
            thinking_items.append((idx, tb))

        tool_items: List[Any] = []
        for tu in tool_uses:
            # Keep the mcp_ prefix on names: history tool_use names must
            # match the prefixed names advertised to the model, or it
            # loops "correcting" them.
            tool_items.append((tu.get("index"), {
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu.get("input", {}),
            }))

        text_block = (
            {"type": "text", "text": text.rstrip()} if text.strip() else None
        )

        have_order = bool(thinking_items) and all(
            i is not None for i, _ in thinking_items
        )
        if not have_order:
            content = [b for _, b in thinking_items]
            if text_block is not None:
                content.append(text_block)
            content.extend(b for _, b in tool_items)
            return content

        known = [float(i) for i, _ in thinking_items]
        known += [float(i) for i, _ in tool_items if i is not None]

        # Per-block text segments: emit each text block at ITS OWN original
        # index.  A segment emptied by sanitization (e.g. a text block that
        # contained only a fabricated tool-call fence) still has to occupy
        # its slot when a later thinking block exists, otherwise that
        # thinking block shifts left out of its original position — the
        # exact modification the API rejects.
        seg_items: List[Any] = []
        if text_segments:
            max_think = max(float(i) for i, _ in thinking_items)
            for s_idx, s_text in text_segments:
                if s_idx is None:
                    continue
                if s_text and s_text.strip():
                    seg_items.append(
                        (float(s_idx), {"type": "text", "text": s_text.rstrip()}))
                elif float(s_idx) < max_think:
                    seg_items.append((float(s_idx), {
                        "type": "text",
                        "text": "[a fabricated tool-call block was removed "
                                "here; the command was executed via the real "
                                "tool API — see the tool results below]",
                    }))
            known += [i for i, _ in seg_items]
        elif text_block is not None and text_index is not None:
            # The merged text occupies a real content-block slot: synthetic
            # tool_use blocks must land strictly AFTER it, or the stable
            # sort slots a fabricated call between thinking and text on the
            # tied key — displacing real blocks from their positions.
            known.append(float(text_index))
        next_synthetic = max(known) + 1.0

        items: List[Any] = [(float(i), b) for i, b in thinking_items]
        for i, b in tool_items:
            if i is None:
                # A synthesized tool_use (e.g. a hallucination-correction
                # fake call) never appeared in the model output — placing
                # it after every real block keeps the real blocks in their
                # original positions.
                items.append((next_synthetic, b))
                next_synthetic += 1.0
            else:
                items.append((float(i), b))
        if seg_items:
            items.extend(seg_items)
        elif text_block is not None:
            if text_index is not None:
                t_idx = float(text_index)
            else:
                real_tools = [float(i) for i, _ in tool_items if i is not None]
                # Best guess without metadata: after the leading thinking
                # run, before the first tool_use — the classic shape.
                t_idx = (min(real_tools) - 0.5) if real_tools else (max(known) + 0.5)
            items.append((t_idx, text_block))
        items.sort(key=lambda pair: pair[0])
        return [b for _, b in items]

    def supports_feature(self, feature_name: str) -> bool:
        """Query whether this provider supports a named capability.

        Known feature names:
          - ``thinking``            — extended thinking / chain of thought
          - ``adaptive_thinking``   — adaptive effort thinking
          - ``extended_context``    — larger-than-default context window
          - ``cache_control``       — prompt caching
          - ``assistant_prefill``   — conversation ending with assistant msg
          - ``image_tool_results``  — tool_result messages may carry image
            content blocks (vision input from tools like render_diagram)

        Default returns False.  Providers override to report their caps.
        """
        return False

    @property
    def provider_name(self) -> str:
        """Human-readable name for logging (e.g. 'bedrock', 'anthropic')."""
        return self.__class__.__name__
