"""
Bedrock LLM Provider — streams responses via boto3 invoke_model_with_response_stream.

Extracted from StreamingToolExecutor to separate API-specific code from
orchestration logic.  This provider:
  - Builds Bedrock-specific request bodies (anthropic_version, tools, thinking)
  - Handles retry with exponential backoff for rate limits and timeouts
  - Parses the boto3 chunked stream into normalized StreamEvent objects
  - Formats conversation messages (assistant turns, tool results)
  - Manages prompt caching within Bedrock's 4-block limit
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import copy
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    LLMProvider,
    ProviderConfig,
    ProcessingEvent,
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
from app.providers.bedrock_region_router import BedrockRegionRouter

logger = get_mode_aware_logger(__name__)


# Dedicated thread pool for Bedrock API calls.  Isolates blocking boto3
# operations (connect + stream reads) from the default asyncio executor
# so that tool execution, MCP communication, and health checks never
# stall waiting for a free thread.
#
# Size: 8 is enough for typical concurrency (each request uses ~2 threads:
# one for the initial connect, one for iterating the stream).  Override
# via BEDROCK_THREAD_POOL_SIZE for high-concurrency deployments.
_BEDROCK_POOL_SIZE = int(os.environ.get("BEDROCK_THREAD_POOL_SIZE", "8"))
_bedrock_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_BEDROCK_POOL_SIZE, thread_name_prefix="bedrock-io"
)

class BedrockProvider(LLMProvider):
    """Streams Claude responses via AWS Bedrock."""

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        aws_profile: str = "ziya",
        region: str = "us-west-2",
    ):
        self.model_id = model_id
        self.model_config = model_config
        self._region = region
        self._aws_profile = aws_profile

        from app.agents.models import ModelManager

        try:
            self.bedrock = ModelManager._get_persistent_bedrock_client(
                aws_profile=aws_profile,
                region=region,
                model_id=model_id,
                model_config=model_config,
            )
            logger.debug("BedrockProvider: using ModelManager wrapped client")
        except Exception as e:
            logger.warning(f"BedrockProvider: wrapped client failed ({e}), falling back to direct boto3")
            import boto3
            from botocore.config import Config as BotoConfig
            self.bedrock = session.client(
                "bedrock-runtime",
                region_name=region,
                config=BotoConfig(
                    max_pool_connections=25,
                    retries={'max_attempts': 2, 'mode': 'adaptive'},
                ),
            )

    # ------------------------------------------------------------------
        # Multi-region router — activates only when model_config has
        # multiple region prefixes in model_id (e.g. {"us": ..., "eu": ...}).
        self._region_router = BedrockRegionRouter(
            model_config=model_config, aws_profile=aws_profile, primary_region=region,
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
        body = self._build_request_body(messages, system_content, tools, config)

        # Single-attempt with extended-context escalation.
        # Transient errors (throttle, timeout, overloaded) are surfaced as
        # ErrorEvent for StreamingToolExecutor to handle with its own
        # intelligent backoff — no retry here to avoid amplification.
        connect_timeout = int(os.environ.get("BEDROCK_CONNECT_TIMEOUT", "180"))
        response = None

        # Scale connect timeout for large payloads.  Bedrock can take
        # several minutes to begin streaming when ingesting >200K tokens.
        body_size = len(json.dumps(body))
        if body_size > 800_000:  # ~200K tokens at ~4 chars/token
            connect_timeout = max(connect_timeout, 600)

        for _attempt in range(1):  # Single attempt; loop kept for break-on-success
            try:
                api_params = {
                    "modelId": self.model_id,
                    "body": json.dumps(body),
                }
                # Run the synchronous boto3 call in a thread so it doesn't
                # block the event loop while waiting for the Bedrock API to
                # start streaming (can be slow for extended-context requests).
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        _bedrock_executor,
                        lambda: self.bedrock.invoke_model_with_response_stream(**api_params),
                    ),
                    timeout=connect_timeout,
                )
                break
            except Exception as e:
                error_str = str(e)
                classified = self._classify_error(error_str)

                # Safety net: if CustomBedrockClient didn't handle context limit
                # (e.g. no conversation_id available), try once with extended context.
                # The primary escalation logic lives in CustomBedrockClient; this
                # only fires when that path was skipped entirely.
                if (
                    classified == ErrorType.CONTEXT_LIMIT
                    and self.model_config.get("supports_extended_context")
                ):
                    header = self.model_config.get("extended_context_header")
                    if header and "anthropic_beta" not in json.dumps(body):
                        logger.info(f"BedrockProvider: safety-net extended context attempt ({header})")
                        body["anthropic_beta"] = [header]
                        try:
                            api_params["body"] = json.dumps(body)
                            response = await asyncio.wait_for(
                                asyncio.get_event_loop().run_in_executor(
                                    _bedrock_executor,
                                    lambda: self.bedrock.invoke_model_with_response_stream(**api_params),
                                ),
                                timeout=connect_timeout,
                            )
                            break
                        except Exception:
                            pass  # fall through to ErrorEvent

                # Region failover: on throttle/overloaded, try an alternate
                # region before surfacing the error to the orchestrator.
                # This is a single failover attempt — not a retry loop.
                if (
                    classified in (ErrorType.THROTTLE, ErrorType.OVERLOADED)
                    and self._region_router.enabled
                ):
                    self._region_router.report_throttle(self._region)
                    alt_endpoint = self._region_router.select_endpoint(exclude=self._region)
                    if alt_endpoint:
                        alt_client = self._region_router.get_client_for_region(alt_endpoint.region)
                        if alt_client:
                            logger.info(
                                f"BedrockProvider: region failover {self._region} → "
                                f"{alt_endpoint.region} ({alt_endpoint.model_id})"
                            )
                            try:
                                alt_params = {
                                    "modelId": alt_endpoint.model_id,
                                    "body": json.dumps(body),
                                }
                                response = await asyncio.wait_for(
                                    asyncio.get_event_loop().run_in_executor(
                                        _bedrock_executor,
                                        lambda: alt_client.invoke_model_with_response_stream(**alt_params),
                                    ),
                                    timeout=connect_timeout,
                                )
                                self._region_router.report_success(alt_endpoint.region)
                                break
                            except Exception as alt_e:
                                logger.warning(f"BedrockProvider: failover to {alt_endpoint.region} also failed: {alt_e}")

                yield ErrorEvent(
                    message=error_str,
                    error_type=classified,
                    retryable=classified in (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED),
                )
                return

        if response is None:
            yield ErrorEvent(message="No response after retries", error_type=ErrorType.UNKNOWN)
            return

        # Parse the boto3 stream into normalized events
        async for event in self._parse_stream(response, config):
            yield event

        # Successful completion — reward the region that served the request
        if self._region_router.enabled:
            self._region_router.report_success(self._region)

    def build_assistant_message(
        self,
        text: str,
        tool_uses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        content_blocks: List[Dict[str, Any]] = []
        if text.strip():
            content_blocks.append({"type": "text", "text": text.rstrip()})
        for tu in tool_uses:
            name = tu["name"]
            # Bedrock expects names without mcp_ prefix
            if name.startswith("mcp_"):
                name = name[4:]
            content_blocks.append({
                "type": "tool_use",
                "id": tu["id"],
                "name": name,
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
        """Bedrock prompt caching: max 4 cache_control blocks.

        System prompt uses 1 block.  We place 1 block at a conversation
        boundary, leaving 2 blocks as headroom.
        """
        if iteration == 0 or len(messages) < 6:
            return messages

        messages = copy.deepcopy(messages)

        # Strip existing markers
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block.pop("cache_control", None)

        # Place marker at boundary (4 messages from end)
        cache_boundary = len(messages) - 4
        if cache_boundary <= 0:
            return messages

        boundary_msg = messages[cache_boundary]
        content = boundary_msg.get("content")
        if isinstance(content, str):
            boundary_msg["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
        elif isinstance(content, list) and content:
            last_block = content[-1]
            if isinstance(last_block, dict):
                last_block["cache_control"] = {"type": "ephemeral"}

        return messages

    def supports_feature(self, feature_name: str) -> bool:
        feature_map = {
            "thinking": self.model_config.get("supports_thinking", False),
            "adaptive_thinking": self.model_config.get("supports_adaptive_thinking", False),
            "extended_context": self.model_config.get("supports_extended_context", False),
            "cache_control": True,  # Bedrock Claude always supports caching
            "assistant_prefill": self.model_config.get("supports_assistant_prefill", True),
        }
        return bool(feature_map.get(feature_name, False))

    @property
    def provider_name(self) -> str:
        return "bedrock"

    @property
    def region_routing_status(self) -> Dict[str, Any]:
        """Diagnostics for multi-region routing state."""
        return self._region_router.status()

    @property
    def region_router(self) -> BedrockRegionRouter:
        return self._region_router

    # ------------------------------------------------------------------
    # Internal: request body building
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": config.max_output_tokens,
            "messages": self.prepare_cache_control(messages, config.iteration),
        }

        # System prompt with cache control
        if system_content:
            if len(system_content) > 1024:
                body["system"] = [{
                    "type": "text",
                    "text": system_content,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                body["system"] = system_content

        # Temperature
        if config.temperature is not None:
            body["temperature"] = config.temperature

        # Thinking configuration
        if config.thinking:
            self._apply_thinking(body, config.thinking)

        # Tools
        if tools and not config.suppress_tools:
            body["tools"] = tools
            body["tool_choice"] = {"type": "auto"}

        return body

    def _apply_thinking(self, body: Dict[str, Any], thinking: "ThinkingConfig") -> None:
        from app.providers.base import ThinkingConfig  # avoid circular at module level

        if thinking.mode == "adaptive":
            body["thinking"] = {"type": "adaptive"}
            if thinking.effort in ("low", "medium", "high", "max"):
                body.setdefault("output_config", {})["effort"] = thinking.effort
                body.setdefault("anthropic_beta", [])
                if "effort-2025-11-24" not in body["anthropic_beta"]:
                    body["anthropic_beta"].append("effort-2025-11-24")
        elif thinking.mode == "enabled" and thinking.enabled:
            body["thinking"] = {"type": "enabled", "budget_tokens": thinking.budget_tokens}

    # ------------------------------------------------------------------
    # Internal: stream parsing
    # ------------------------------------------------------------------

    async def _parse_stream(
        self,
        response: Any,
        config: ProviderConfig,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Parse boto3 streaming response into normalized events."""
        stream_body = response["body"]

        # Adaptive timeout: when thinking is enabled, the model may go
        # silent for minutes during extended computation.  Use a short
        # poll interval so we can emit ProcessingEvent heartbeats, but
        # allow a much longer total wait before declaring the stream dead.
        thinking_enabled = (
            config.thinking is not None
            and (config.thinking.enabled or config.thinking.mode == "adaptive")
        )
        poll_interval = 15 if thinking_enabled else int(os.environ.get("STREAM_STALL_TIMEOUT", "120"))
        max_silence = int(os.environ.get("BEDROCK_MAX_THINKING_WAIT", "900")) if thinking_enabled else poll_interval
        silence_elapsed = 0.0

        # Active tool tracking within this single response
        active_tools: Dict[str, Dict[str, Any]] = {}  # tool_id -> {name, partial_json, index}

        stream_iter = iter(stream_body)
        in_thinking_block = False

        # Pending read task — ensures we never call next(stream_iter) concurrently.
        # When a timeout occurs we must NOT start a new to_thread call; instead we
        # keep awaiting the same task until it completes or the total silence budget
        # is exhausted.
        pending_read: Optional[asyncio.Task] = None

        while True:
            try:
                # Start a new read only if we don't already have one in-flight
                if pending_read is None:
                    def _next_event(it=stream_iter):
                        try:
                            return next(it)
                        except StopIteration:
                            return None
                    loop = asyncio.get_event_loop()
                    pending_read = asyncio.ensure_future(
                        loop.run_in_executor(_bedrock_executor, _next_event)
                    )

                try:
                    event = await asyncio.wait_for(
                        asyncio.shield(pending_read),
                        timeout=poll_interval,
                    )
                except asyncio.TimeoutError:
                    # The read is still in-flight in the thread pool — do NOT
                    # cancel it or start another one.  Just update silence
                    # tracking and emit a heartbeat.
                    silence_elapsed += poll_interval

                    if silence_elapsed >= max_silence:
                        # Give up waiting — cancel the dangling read
                        pending_read.cancel()
                        pending_read = None
                        yield ErrorEvent(
                            message=f"Stream stalled — no data for {int(silence_elapsed)}s"
                                  + (" (thinking enabled)" if thinking_enabled else ""),
                            error_type=ErrorType.READ_TIMEOUT,
                            retryable=thinking_enabled,
                        )
                        return

                    phase = "thinking" if in_thinking_block else ("processing" if thinking_enabled else "stalled")
                    yield ProcessingEvent(elapsed_seconds=silence_elapsed, phase=phase)
                    continue  # retry awaiting the SAME pending_read

                # Read completed successfully — clear the pending task
                pending_read = None

            except asyncio.TimeoutError:
                # Outer safety net — should not be reached with the inner handling,
                # but guard against edge cases.
                continue

            if event is None:
                break  # stream exhausted

            # Got data — reset silence counter
            silence_elapsed = 0.0

            if "chunk" not in event:
                continue

            chunk_bytes = event["chunk"]["bytes"]
            chunk_str = self._decode_chunk_bytes(chunk_bytes)
            chunk = json.loads(chunk_str)

            # Usage metrics
            if "amazon-bedrock-invocationMetrics" in chunk:
                m = chunk["amazon-bedrock-invocationMetrics"]
                yield UsageEvent(
                    input_tokens=m.get("inputTokenCount", 0),
                    output_tokens=m.get("outputTokenCount", 0),
                    cache_read_tokens=m.get("cacheReadInputTokenCount", 0),
                    cache_write_tokens=m.get("cacheWriteInputTokenCount", 0),
                )

            chunk_type = chunk.get("type", "")

            if chunk_type == "content_block_start":
                cb = chunk.get("content_block", {})
                idx = chunk.get("index", 0)
                block_type = cb.get("type", "")
                if block_type == "thinking":
                    in_thinking_block = True
                elif block_type == "tool_use":
                    tool_id = cb.get("id", "")
                    tool_name = cb.get("name", "")
                    active_tools[tool_id] = {
                        "name": tool_name,
                        "partial_json": "",
                        "index": idx,
                    }
                    yield ToolUseStart(id=tool_id, name=tool_name, index=idx)

            elif chunk_type == "content_block_delta":
                delta = chunk.get("delta", {})
                idx = chunk.get("index", 0)
                delta_type = delta.get("type", "")

                if delta_type == "text_delta":
                    yield TextDelta(content=delta.get("text", ""))

                elif delta_type == "thinking_delta":
                    yield ThinkingDelta(content=delta.get("thinking", ""))

                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    # Accumulate for ToolUseEnd
                    for tid, tdata in active_tools.items():
                        if tdata["index"] == idx:
                            tdata["partial_json"] += partial
                            break
                    yield ToolUseInput(partial_json=partial, index=idx)

            elif chunk_type == "content_block_stop":
                idx = chunk.get("index", 0)
                # If a thinking block just finished, clear the flag
                if in_thinking_block:
                    in_thinking_block = False
                # Find the tool that just finished
                finished_id = None
                for tid, tdata in active_tools.items():
                    if tdata["index"] == idx:
                        finished_id = tid
                        break
                if finished_id:
                    tdata = active_tools.pop(finished_id)
                    try:
                        parsed_input = json.loads(tdata["partial_json"]) if tdata["partial_json"] else {}
                    except json.JSONDecodeError:
                        parsed_input = {}
                    yield ToolUseEnd(
                        id=finished_id,
                        name=tdata["name"],
                        input=parsed_input,
                        index=idx,
                    )

            elif chunk_type == "message_stop":
                stop_reason = chunk.get("stop_reason", chunk.get("amazon-bedrock-stop-reason", "end_turn"))
                yield StreamEnd(stop_reason=stop_reason)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_chunk_bytes(chunk_bytes: Any) -> str:
        if isinstance(chunk_bytes, bytes):
            return chunk_bytes.decode("utf-8")
        if isinstance(chunk_bytes, str):
            return chunk_bytes
        raise TypeError(f"Unexpected chunk type: {type(chunk_bytes)}")

    @staticmethod
    def _classify_error(error_str: str) -> ErrorType:
        lowered = error_str.lower()
        if any(s in error_str for s in ("ThrottlingException", "Too many tokens", "Too many requests")) or "rate limit" in lowered:
            return ErrorType.THROTTLE
        if any(s in error_str for s in ("Input is too long", "too large", "prompt is too long")):
            return ErrorType.CONTEXT_LIMIT
        if any(s in error_str for s in ("Read timed out", "ReadTimeoutError")) or "timeout" in lowered:
            return ErrorType.READ_TIMEOUT
        if "overloaded" in lowered or "529" in error_str or "ServiceUnavailableException" in error_str:
            return ErrorType.OVERLOADED
        return ErrorType.UNKNOWN
