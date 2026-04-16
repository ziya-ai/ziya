"""
OpenAI-on-Bedrock LLM Provider — streams responses via invoke_model.

Models that speak the OpenAI Chat Completions wire format on Bedrock
(DeepSeek v3/v3.2, Kimi, MiniMax, GLM, Qwen, OpenAI-GPT-OSS) use
invoke_model / invoke_model_with_response_stream with a JSON body
that follows the OpenAI schema — NOT the Converse API.

The Converse API is a unified abstraction but can silently mangle
newlines and whitespace for models whose native format is OpenAI.
This provider uses the native wire format to preserve response
fidelity.

Key differences from NovaBedrockProvider (Converse API):
  - Uses invoke_model_with_response_stream, not converse_stream
  - Request body follows OpenAI Chat Completions schema
  - Response chunks use choices[].delta.content (not contentBlockDelta)
  - Reasoning/thinking content arrives in delta.reasoning (DeepSeek R1)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.providers.base import (
    ErrorEvent, ErrorType, LLMProvider, ProcessingEvent, ProviderConfig,
    StreamEnd, StreamEvent, TextDelta, ThinkingDelta, UsageEvent,
)
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

_POOL_SIZE = int(os.environ.get("BEDROCK_THREAD_POOL_SIZE", "8"))
_oai_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_POOL_SIZE, thread_name_prefix="oai-bedrock-io"
)


class OpenAIBedrockProvider(LLMProvider):
    """Streams OpenAI-format model responses via Bedrock invoke_model."""

    def __init__(self, model_id: str, model_config: Dict[str, Any],
                 aws_profile: str = "ziya", region: str = "us-west-2"):
        self.model_id = model_id
        self.model_config = model_config
        self._region = region
        self._aws_profile = aws_profile

        # OpenAI-format models on Bedrock are often region-restricted.
        # Honor model config region override if present.
        effective_region = model_config.get("region", region)

        from app.providers.bedrock_client_cache import get_persistent_bedrock_client
        try:
            self.bedrock = get_persistent_bedrock_client(
                aws_profile=aws_profile, region=effective_region,
                model_id=model_id, model_config=model_config,
            )
        except Exception as e:
            logger.warning(
                f"OpenAIBedrockProvider: persistent client failed ({e}), "
                f"falling back to boto3"
            )
            import boto3
            from botocore.config import Config as BotoConfig
            self.bedrock = boto3.client(
                "bedrock-runtime", region_name=effective_region,
                config=BotoConfig(
                    max_pool_connections=25,
                    retries={"max_attempts": 2, "mode": "adaptive"},
                ),
            )

    # -- LLMProvider interface ---------------------------------------------

    async def stream_response(self, messages, system_content, tools, config):
        body = self._build_request_body(messages, system_content, tools, config)
        timeout = int(os.environ.get("BEDROCK_CONNECT_TIMEOUT", "180"))
        logger.debug(
            f"OpenAIBedrockProvider: invoke_model_with_response_stream "
            f"{self.model_id}, msgs={len(messages)}"
        )
        t0 = time.time()

        # The persistent client may be a wrapper; get the raw boto3 client.
        client = getattr(self.bedrock, 'client', self.bedrock)

        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    _oai_executor,
                    lambda: client.invoke_model_with_response_stream(
                        modelId=self.model_id,
                        body=json.dumps(body),
                        contentType="application/json",
                        accept="application/json",
                    ),
                ),
                timeout=timeout,
            )
        except Exception as e:
            ct = self._classify_error(str(e))
            logger.warning(
                f"OpenAIBedrockProvider: failed {time.time()-t0:.1f}s — "
                f"{ct.name}: {str(e)[:200]}"
            )
            yield ErrorEvent(
                message=str(e), error_type=ct,
                retryable=ct in (
                    ErrorType.THROTTLE, ErrorType.READ_TIMEOUT,
                    ErrorType.OVERLOADED,
                ),
            )
            return

        async for ev in self._parse_stream(response):
            yield ev

    def build_assistant_message(self, text, tool_uses):
        # OpenAI format: single content string on the assistant message.
        # Tool calls are not supported for most of these models on Bedrock.
        return {"role": "assistant", "content": text.rstrip() if text else ""}

    def build_tool_result_message(self, tool_results):
        # Tool results aren't typically used for OpenAI-format models on
        # Bedrock (they don't support native function calling), but
        # implement the interface for completeness.
        parts = []
        for tr in tool_results:
            parts.append(
                f"Tool result ({tr.get('tool_use_id', '?')}): "
                f"{tr.get('content', '')}"
            )
        return {"role": "user", "content": "\n".join(parts)}

    def supports_feature(self, feature_name):
        m = {
            "thinking": self.model_config.get("supports_thinking", False),
            "assistant_prefill": self.model_config.get(
                "supports_assistant_prefill", False
            ),
        }
        return bool(m.get(feature_name, False))

    @property
    def provider_name(self):
        return "openai_bedrock"

    # -- Request building --------------------------------------------------

    def _build_request_body(self, messages, system_content, tools, config):
        """Build an OpenAI Chat Completions format request body."""
        openai_messages = []

        # System message
        if system_content:
            openai_messages.append({"role": "system", "content": system_content})

        # Conversation messages
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Handle Anthropic-style content block arrays
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif "text" in block:
                            text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            if not content:
                continue

            openai_messages.append({"role": role, "content": content})

        # Build body
        model_max = self.model_config.get("max_output_tokens", 4096)
        effective_max = min(config.max_output_tokens, model_max)

        # DeepSeek and most OpenAI-format models on Bedrock use "max_tokens".
        # Only newer OpenAI-native models use "max_completion_tokens".
        # Default to "max_tokens" for broadest compatibility.
        max_tokens_key = self.model_config.get(
            "max_tokens_param", "max_tokens"
        )

        body: Dict[str, Any] = {
            "messages": openai_messages,
            max_tokens_key: effective_max,
        }

        if config.temperature is not None:
            body["temperature"] = config.temperature

        return body

    # -- Stream parsing ----------------------------------------------------

    async def _parse_stream(self, response):
        """Parse OpenAI-format streaming response from invoke_model."""
        stream = response.get("body", [])
        poll = int(os.environ.get("STREAM_STALL_TIMEOUT", "120"))
        it = iter(stream)
        pending = None
        silence = 0.0
        chunk_count = 0
        yielded_end = False

        while True:
            if pending is None:
                def _nxt(i=it):
                    try:
                        return next(i)
                    except StopIteration:
                        return None
                pending = asyncio.ensure_future(
                    asyncio.get_event_loop().run_in_executor(
                        _oai_executor, _nxt
                    )
                )

            try:
                try:
                    ev = await asyncio.wait_for(
                        asyncio.shield(pending), timeout=poll
                    )
                except asyncio.TimeoutError:
                    silence += poll
                    pending.cancel()
                    pending = None
                    yield ErrorEvent(
                        message=f"Stream stalled {int(silence)}s",
                        error_type=ErrorType.READ_TIMEOUT,
                        retryable=True,
                    )
                    return
                pending = None
            except asyncio.CancelledError:
                if pending:
                    pending.cancel()
                raise

            if ev is None:
                break
            silence = 0.0
            chunk_count += 1
            if chunk_count <= 3:
                raw_preview = str(ev)[:300]
                logger.info(f"OpenAIBedrockProvider: chunk #{chunk_count}: {raw_preview}")

            # Parse the raw event bytes
            try:
                raw = ev.get("chunk", {}).get("bytes", b"")
                if not raw:
                    continue
                chunk = json.loads(raw)
            except (json.JSONDecodeError, AttributeError, KeyError, TypeError) as e:
                logger.debug(f"OpenAIBedrockProvider: unparseable chunk: {e}")
                continue

            # Extract content from OpenAI Chat Completions format
            choices = chunk.get("choices", [])
            if not choices:
                # Check for usage in non-choice events
                usage = chunk.get("usage")
                if usage:
                    yield UsageEvent(
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                    )
                continue

            choice = choices[0]
            # Bedrock OpenAI-format models vary: some use "delta" (standard
            # OpenAI streaming), others use "message" (DeepSeek R1).
            delta = choice.get("delta") or choice.get("message") or {}
            finish_reason = choice.get("finish_reason") or choice.get("stop_reason")

            # Reasoning / thinking content (DeepSeek R1)
            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning:
                yield ThinkingDelta(content=reasoning)

            # Regular text content — yield as-is, preserving newlines
            content = delta.get("content")
            if content:
                yield TextDelta(content=content)

            # Stream end
            if finish_reason:
                stop_reason = "end_turn"
                if finish_reason == "tool_calls":
                    stop_reason = "tool_use"
                elif finish_reason == "length":
                    stop_reason = "max_tokens"
                elif finish_reason == "stop":
                    stop_reason = "end_turn"

                # Extract final usage if present
                usage = chunk.get("usage")
                if usage:
                    yield UsageEvent(
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                    )

                yield StreamEnd(stop_reason=stop_reason)
                yielded_end = True

        if not yielded_end:
            if chunk_count == 0:
                logger.warning(
                    f"OpenAIBedrockProvider: stream returned 0 chunks — "
                    f"model may have returned an error or empty response"
                )
            else:
                logger.warning(
                    f"OpenAIBedrockProvider: stream ended after {chunk_count} "
                    f"chunks without finish_reason"
                )
            yield StreamEnd(stop_reason="end_turn")

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _classify_error(s):
        lo = s.lower()
        if any(x in s for x in (
            "ThrottlingException", "Too many tokens", "Too many requests"
        )) or "rate limit" in lo:
            return ErrorType.THROTTLE
        if any(x in s for x in (
            "Input is too long", "too large", "prompt is too long"
        )):
            return ErrorType.CONTEXT_LIMIT
        if any(x in s for x in (
            "Read timed out", "ReadTimeoutError"
        )) or "timeout" in lo:
            return ErrorType.READ_TIMEOUT
        if "overloaded" in lo or "529" in s or "ServiceUnavailableException" in s:
            return ErrorType.OVERLOADED
        return ErrorType.UNKNOWN
