"""
OpenAI-compatible LLM Provider — streams via the openai Python SDK.

Works with:
  - OpenAI directly (api.openai.com)
  - OpenRouter (openrouter.ai/api/v1) via OPENAI_BASE_URL override
  - Any OpenAI-compatible endpoint (vLLM, Together, etc.)

The orchestrator passes messages in Anthropic format (tool_use/tool_result
content blocks).  This provider converts them to OpenAI format
(tool_calls array, role=tool messages) transparently.
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
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)


class OpenAIDirectProvider(LLMProvider):
    """Streams responses via the OpenAI chat completions API."""

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.model_id = model_id
        self.model_config = model_config

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for the OpenAI endpoint. "
                "Install it with: pip install openai"
            )

        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        resolved_base = base_url or os.getenv("OPENAI_BASE_URL")

        client_kwargs: Dict[str, Any] = {}
        if resolved_key:
            client_kwargs["api_key"] = resolved_key
        if resolved_base:
            client_kwargs["base_url"] = resolved_base

        self.client = AsyncOpenAI(**client_kwargs)
        logger.info(
            f"OpenAIDirectProvider: model={model_id}, "
            f"base_url={resolved_base or 'default'}"
        )

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

        max_retries = 4
        base_delay = 2

        for retry_attempt in range(max_retries + 1):
            try:
                async for event in self._do_stream(request_kwargs):
                    yield event
                return
            except Exception as e:
                error_str = str(e)
                classified = self._classify_error(error_str)
                retryable = classified in (
                    ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED,
                )
                if retryable and retry_attempt < max_retries:
                    delay = base_delay * (2 ** retry_attempt) + 1
                    logger.warning(
                        f"OpenAIDirectProvider: {classified.name} retry "
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
        msg: Dict[str, Any] = {
            "role": "assistant",
            "content": text.rstrip() if text.strip() else None,
        }
        if tool_uses:
            msg["tool_calls"] = [
                {
                    "id": tu["id"],
                    "type": "function",
                    "function": {
                        "name": tu["name"],
                        "arguments": json.dumps(tu.get("input", {})),
                    },
                }
                for tu in tool_uses
            ]
        return msg

    def build_tool_result_message(
        self,
        tool_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if len(tool_results) == 1:
            tr = tool_results[0]
            return {
                "role": "tool",
                "tool_call_id": tr["tool_use_id"],
                "content": tr["content"],
            }
        # Multiple tool results: return list wrapper for orchestrator
        return {
            "role": "_multi_tool_results",
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": tr["tool_use_id"],
                    "content": tr["content"],
                }
                for tr in tool_results
            ],
        }

    def supports_feature(self, feature_name: str) -> bool:
        return bool({
            "thinking": self.model_config.get("supports_thinking", False),
            "assistant_prefill": True,
            "cache_control": False,
            "extended_context": False,
            "adaptive_thinking": False,
        }.get(feature_name, False))

    @property
    def provider_name(self) -> str:
        return "openai"

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
        openai_messages = []
        if system_content:
            openai_messages.append({"role": "system", "content": system_content})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            # Convert Anthropic tool_result blocks → OpenAI tool messages
            if role == "user" and isinstance(content, list):
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": block.get("content", ""),
                            })
                    continue

            # Convert Anthropic assistant tool_use blocks → OpenAI tool_calls
            if role == "assistant" and isinstance(content, list):
                text_parts, tool_calls = [], []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                a_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n".join(text_parts) if text_parts else None,
                }
                if tool_calls:
                    a_msg["tool_calls"] = tool_calls
                openai_messages.append(a_msg)
                continue

            openai_messages.append({"role": role, "content": content})

        kwargs: Dict[str, Any] = {
            "model": self.model_id,
            "messages": openai_messages,
            "max_tokens": config.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if tools and not config.suppress_tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]
            kwargs["tool_choice"] = "auto"
        return kwargs

    # ------------------------------------------------------------------
    # Internal: stream parsing
    # ------------------------------------------------------------------

    async def _do_stream(self, request_kwargs: Dict[str, Any]) -> AsyncGenerator[StreamEvent, None]:
        active_tool_calls: Dict[int, Dict[str, Any]] = {}

        stream = await self.client.chat.completions.create(**request_kwargs)

        async for chunk in stream:
            if not chunk.choices:
                if chunk.usage:
                    u = chunk.usage
                    details = getattr(u, "prompt_tokens_details", None)
                    yield UsageEvent(
                        input_tokens=getattr(u, "prompt_tokens", 0),
                        output_tokens=getattr(u, "completion_tokens", 0),
                        cache_read_tokens=getattr(details, "cached_tokens", 0) if details else 0,
                    )
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta and delta.content:
                yield TextDelta(content=delta.content)

            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in active_tool_calls:
                        active_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    entry = active_tool_calls[idx]
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name and not entry["name"]:
                            entry["name"] = tc.function.name
                            yield ToolUseStart(id=entry["id"], name=entry["name"], index=idx)
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments
                            yield ToolUseInput(partial_json=tc.function.arguments, index=idx)

            if choice.finish_reason:
                for idx, tc in list(active_tool_calls.items()):
                    try:
                        parsed = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    yield ToolUseEnd(id=tc["id"], name=tc["name"], input=parsed, index=idx)
                active_tool_calls.clear()
                yield StreamEnd(stop_reason=choice.finish_reason)

    @staticmethod
    def _classify_error(error_str: str) -> ErrorType:
        lowered = error_str.lower()
        if "429" in error_str or "rate" in lowered or "too many" in lowered:
            return ErrorType.THROTTLE
        if "503" in error_str or "overloaded" in lowered:
            return ErrorType.OVERLOADED
        if "timeout" in lowered:
            return ErrorType.READ_TIMEOUT
        if "context" in lowered and ("long" in lowered or "large" in lowered or "limit" in lowered):
            return ErrorType.CONTEXT_LIMIT
        return ErrorType.UNKNOWN
