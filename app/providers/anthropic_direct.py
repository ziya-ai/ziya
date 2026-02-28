"""
Anthropic Direct LLM Provider — streams responses via the native anthropic SDK.

Uses anthropic.AsyncAnthropic for direct API access (no Bedrock intermediary).
Supports prompt caching, native tool calling, and extended thinking.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    LLMProvider,
    ProviderConfig,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)


class AnthropicDirectProvider(LLMProvider):
    """Streams Claude responses via the Anthropic API directly."""

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        api_key: Optional[str] = None,
    ):
        self.model_id = model_id
        self.model_config = model_config

        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is required for the Anthropic endpoint. "
                "Install it with: pip install anthropic"
            )

        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment")

        self.client = anthropic.AsyncAnthropic(api_key=resolved_key)
        logger.info(f"AnthropicDirectProvider: model={model_id}")

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    async def stream_response(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        request_kwargs = self._build_request(messages, system_content, tools, config)

        # Pre-flight token estimate to avoid hitting the API hard limit
        preflight_error = self._check_context_limit(request_kwargs)
        if preflight_error:
            yield preflight_error
            return

        max_retries = 4
        base_delay = 2

        for retry_attempt in range(max_retries + 1):
            try:
                async for event in self._do_stream(request_kwargs):
                    yield event
                return  # success
            except Exception as e:
                error_str = str(e)
                classified = self._classify_error(error_str)
                retryable = classified in (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED)

                if retryable and retry_attempt < max_retries:
                    if classified == ErrorType.THROTTLE:
                        delay = base_delay * (2 ** retry_attempt) + 2
                    else:
                        delay = 2 * (retry_attempt + 1)
                    logger.warning(
                        f"AnthropicDirectProvider: {classified.name} retry "
                        f"{retry_attempt + 1}/{max_retries + 1} after {delay}s"
                    )
                    await asyncio.sleep(delay)
                    continue

                yield ErrorEvent(
                    message=error_str,
                    error_type=classified,
                    retryable=False,
                )
                return

    def build_assistant_message(
        self,
        text: str,
        tool_uses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        content_blocks: List[Dict[str, Any]] = []
        if text.strip():
            content_blocks.append({"type": "text", "text": text.rstrip()})
        for tu in tool_uses:
            content_blocks.append({
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu.get("input", {}),
            })
        return {"role": "assistant", "content": content_blocks}

    def build_tool_result_message(
        self,
        tool_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        blocks = []
        for tr in tool_results:
            blocks.append({
                "type": "tool_result",
                "tool_use_id": tr["tool_use_id"],
                "content": tr["content"],
            })
        return {"role": "user", "content": blocks}

    def prepare_cache_control(
        self,
        messages: List[Dict[str, Any]],
        iteration: int,
    ) -> List[Dict[str, Any]]:
        """Anthropic API has no 4-block limit on cache markers.

        Cache the conversation boundary (second-to-last message) so that
        on multi-turn conversations prior turns get reused.
        """
        if iteration == 0 or len(messages) < 3:
            return messages

        import copy
        messages = copy.deepcopy(messages)

        boundary = messages[-2]
        bc = boundary.get("content")
        if isinstance(bc, str):
            boundary["content"] = [{
                "type": "text",
                "text": bc,
                "cache_control": {"type": "ephemeral"},
            }]
        elif isinstance(bc, list) and bc:
            last_block = bc[-1]
            if isinstance(last_block, dict) and "cache_control" not in last_block:
                last_block["cache_control"] = {"type": "ephemeral"}

        return messages

    def supports_feature(self, feature_name: str) -> bool:
        feature_map = {
            "thinking": self.model_config.get("supports_thinking", False),
            "adaptive_thinking": self.model_config.get("supports_adaptive_thinking", False),
            "extended_context": False,  # Anthropic direct has 200k natively
            "cache_control": True,
            "assistant_prefill": True,
        }
        return bool(feature_map.get(feature_name, False))

    @property
    def provider_name(self) -> str:
        return "anthropic"

    # ------------------------------------------------------------------
    # Internal: request building
    # ------------------------------------------------------------------

    def _build_request(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": config.max_output_tokens,
            "messages": self.prepare_cache_control(messages, config.iteration),
        }

        if system_content:
            kwargs["system"] = [{
                "type": "text",
                "text": system_content,
                "cache_control": {"type": "ephemeral"},
            }]

        if config.temperature is not None:
            kwargs["temperature"] = config.temperature

        if tools and not config.suppress_tools:
            kwargs["tools"] = tools

        if config.thinking:
            if config.thinking.mode == "adaptive":
                # Anthropic direct API: adaptive takes no budget_tokens
                kwargs["thinking"] = {"type": "adaptive"}
            elif config.thinking.mode == "enabled":
                # Standard extended thinking requires budget_tokens
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": config.thinking.budget_tokens}

        return kwargs

    # ------------------------------------------------------------------
    # Internal: stream parsing
    # ------------------------------------------------------------------

    async def _do_stream(
        self,
        request_kwargs: Dict[str, Any],
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run a single streaming request and yield normalized events."""
        active_tools: Dict[int, Dict[str, Any]] = {}  # index -> {id, name, partial_json}

        async with self.client.messages.stream(**request_kwargs) as stream:
            async for event in stream:
                if event.type == "message_start" and hasattr(event, "message"):
                    usage = getattr(event.message, "usage", None)
                    if usage:
                        yield UsageEvent(
                            input_tokens=getattr(usage, "input_tokens", 0),
                            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
                            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0),
                        )

                elif event.type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage:
                        yield UsageEvent(output_tokens=getattr(usage, "output_tokens", 0))

                elif event.type == "content_block_start":
                    cb = event.content_block
                    idx = getattr(event, "index", 0)
                    if cb.type == "tool_use":
                        active_tools[idx] = {
                            "id": cb.id,
                            "name": cb.name,
                            "partial_json": "",
                        }
                        yield ToolUseStart(id=cb.id, name=cb.name, index=idx)

                elif event.type == "content_block_delta":
                    idx = getattr(event, "index", 0)
                    delta = event.delta

                    if delta.type == "text_delta":
                        yield TextDelta(content=delta.text)

                    elif delta.type == "thinking_delta":
                        yield ThinkingDelta(content=getattr(delta, "thinking", ""))

                    elif delta.type == "input_json_delta":
                        partial = delta.partial_json
                        if idx in active_tools:
                            active_tools[idx]["partial_json"] += partial
                        yield ToolUseInput(partial_json=partial, index=idx)

                elif event.type == "content_block_stop":
                    idx = getattr(event, "index", 0)
                    if idx in active_tools:
                        tdata = active_tools.pop(idx)
                        try:
                            parsed = json.loads(tdata["partial_json"]) if tdata["partial_json"] else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        yield ToolUseEnd(
                            id=tdata["id"],
                            name=tdata["name"],
                            input=parsed,
                            index=idx,
                        )

                elif event.type == "message_stop":
                    yield StreamEnd(stop_reason="end_turn")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _estimate_request_tokens(self, request_kwargs: Dict[str, Any]) -> int:
        """Estimate total tokens for a request.

        Tries the Anthropic count_tokens API first (accurate), falls back to
        a character-based heuristic if the API call fails.
        """
        # --- Attempt 1: use Anthropic's count_tokens endpoint (accurate) ---
        try:
            import anthropic as _anthropic
            # Build a sync client for the quick count_tokens call
            sync_client = _anthropic.Anthropic(api_key=self.client.api_key)
            count_kwargs = {
                "model": request_kwargs["model"],
                "messages": request_kwargs["messages"],
            }
            if "system" in request_kwargs:
                count_kwargs["system"] = request_kwargs["system"]
            if "tools" in request_kwargs:
                count_kwargs["tools"] = request_kwargs["tools"]
            if "thinking" in request_kwargs:
                count_kwargs["thinking"] = request_kwargs["thinking"]

            resp = sync_client.messages.count_tokens(**count_kwargs)
            token_count = resp.input_tokens
            logger.info(f"AnthropicDirectProvider: count_tokens API returned {token_count:,} tokens")
            return token_count
        except Exception as e:
            logger.warning(f"AnthropicDirectProvider: count_tokens API failed ({e}), using heuristic")

        # --- Attempt 2: character-based heuristic (fallback) ---
        total_chars = 0
        system = request_kwargs.get("system")
        if isinstance(system, str):
            total_chars += len(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict):
                    total_chars += len(block.get("text", ""))
        for msg in request_kwargs.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(block.get("text", ""))
                        total_chars += len(json.dumps(block.get("input", {}))) if "input" in block else 0
        tools = request_kwargs.get("tools", [])
        if tools:
            # Anthropic's internal tool formatting adds ~2.5-3x overhead vs raw JSON
            total_chars += int(len(json.dumps(tools)) * 2.5)
        return int(total_chars / 3.5)

    def _check_context_limit(self, request_kwargs: Dict[str, Any]) -> Optional[ErrorEvent]:
        """Return an ErrorEvent if the estimated tokens exceed the model's limit."""
        model_limit = self.model_config.get("token_limit", 200000)
        estimated = self._estimate_request_tokens(request_kwargs)
        # Leave headroom for output tokens
        max_output = request_kwargs.get("max_tokens", 16384)
        effective_limit = model_limit - max_output

        if estimated > effective_limit:
            msg = (
                f"Estimated input size ({estimated:,} tokens) exceeds the model's "
                f"effective limit ({effective_limit:,} tokens = {model_limit:,} context "
                f"- {max_output:,} output). Try reducing the number of files in context "
                f"or the number of MCP tools loaded."
            )
            logger.error(f"AnthropicDirectProvider: {msg}")
            return ErrorEvent(message=msg, error_type=ErrorType.CONTEXT_LIMIT, retryable=False)
        
        logger.debug(f"AnthropicDirectProvider: pre-flight estimate {estimated:,} tokens "
                      f"(limit: {effective_limit:,})")
        return None

    @staticmethod
    def _classify_error(error_str: str) -> ErrorType:
        lowered = error_str.lower()
        if "rate" in lowered or "429" in error_str or "too many" in lowered:
            return ErrorType.THROTTLE
        if "overloaded" in lowered or "529" in error_str:
            return ErrorType.OVERLOADED
        if "timeout" in lowered:
            return ErrorType.READ_TIMEOUT
        if "prompt is too long" in lowered or "too large" in lowered:
            return ErrorType.CONTEXT_LIMIT
        return ErrorType.UNKNOWN
