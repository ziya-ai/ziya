"""
OpenAI-Responses-on-Mantle LLM Provider — streams via the bedrock-mantle
gateway's OpenAI Responses API path.

The GPT-5.6 family (Sol/Terra/Luna) on Bedrock is served exclusively
through bedrock-mantle at /openai/v1/responses.  That is the OpenAI
*Responses* API wire format — a third format distinct from both:
  - OpenAIBedrockProvider: OpenAI *Chat Completions* over bedrock-runtime
    invoke_model (gpt-oss, DeepSeek, Kimi, ...)
  - BedrockMantleProvider: *Anthropic Messages* over bedrock-mantle
    (fable5, mythos5)

Authentication is SigV4 with standard AWS credentials, using the same
httpx transport mechanism as BedrockMantleProvider.  The OpenAI SDK's
placeholder Bearer Authorization header is stripped before signing.

Model config opts into this provider with:
    "endpoint_override": "bedrock-mantle",
    "mantle_api": "openai-responses",
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

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
from app.providers.bedrock_mantle import (
    _AsyncSigV4Transport,
    _MANTLE_LIMITS,
    _MANTLE_TIMEOUT,
)
from app.providers.error_scrub import scrub_request_id
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

# The OpenAI SDK appends /responses to the base URL for
# client.responses.create, yielding .../openai/v1/responses.
_MANTLE_OPENAI_BASE_URL = "https://bedrock-mantle.{region}.api.aws/openai/v1"

# Ziya's canonical effort ladder → Responses API reasoning.effort values.
_EFFORT_MAP = {
    "none": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


class _ResponsesSigV4Transport(_AsyncSigV4Transport):
    """SigV4 transport that also strips the OpenAI SDK's Bearer header.

    The SDK requires an api_key at init and injects it as
    "Authorization: Bearer <placeholder>".  botocore excludes
    'authorization' from the canonical signed headers and overwrites it
    with the SigV4 signature, but remove the placeholder explicitly so
    the outgoing request never carries a bogus bearer token.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            del request.headers["authorization"]
        return await super().handle_async_request(request)


class OpenAIResponsesMantleProvider(LLMProvider):
    """OpenAI Responses API over Bedrock Mantle with SigV4 auth."""

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        region: str = "us-east-1",
        aws_profile: Optional[str] = None,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for the GPT-on-Mantle "
                "path. Install it with: pip install openai"
            )

        self.model_id = model_id
        self.model_config = model_config

        base_url = _MANTLE_OPENAI_BASE_URL.format(region=region)
        transport = _ResponsesSigV4Transport(region=region, profile=aws_profile)
        self.client = AsyncOpenAI(
            api_key="unused",  # Required by SDK init; SigV4 transport replaces auth.
            base_url=base_url,
            # Same reasoning as BedrockMantleProvider: an unbounded write can
            # outlive the SigV4 signature it carries, turning a stalled
            # connection into a misleading authentication error.
            http_client=httpx.AsyncClient(
                transport=transport,
                timeout=_MANTLE_TIMEOUT,
                limits=_MANTLE_LIMITS,
            ),
        )
        logger.info(
            f"OpenAIResponsesMantleProvider: model={model_id} base_url={base_url}"
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
        base_delay = 5  # true-doubling throttle backoff: 5, 10, 20, 40s

        for retry_attempt in range(max_retries + 1):
            content_yielded = False
            try:
                async for event in self._do_stream(request_kwargs):
                    if isinstance(event, (TextDelta, ThinkingDelta, ToolUseStart)):
                        content_yielded = True
                    yield event
                return
            except Exception as e:
                error_str = str(e)
                classified = self._classify_error(error_str)
                retryable = classified in (
                    ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED,
                )
                # A from-scratch retry after content has been yielded would
                # APPEND the retry's full response to the partial text the
                # consumer accumulated — duplicated-message corruption.
                # Once content is out, fail loudly instead.
                if retryable and content_yielded:
                    logger.warning(
                        f"OpenAIResponsesMantleProvider: {classified.name} mid-stream "
                        f"after content was yielded — refusing duplicate-producing retry"
                    )
                    retryable = False
                if retryable and retry_attempt < max_retries:
                    delay = base_delay * (2 ** retry_attempt)
                    logger.warning(
                        f"OpenAIResponsesMantleProvider: {classified.name} retry "
                        f"{retry_attempt + 1}/{max_retries + 1} after {delay}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                yield ErrorEvent(
                    message=error_str, error_type=classified, retryable=False,
                )
                return

    def build_assistant_message(
        self,
        text: str,
        tool_uses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Chat-format shape (content + tool_calls); converted to Responses
        # input items (message / function_call) in _build_request.
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
            "assistant_prefill": False,
            "cache_control": False,
            "extended_context": False,
            "adaptive_thinking": False,
        }.get(feature_name, False))

    @property
    def provider_name(self) -> str:
        return "openai_responses_mantle"

    def bind(self, **kwargs):
        """Compatibility no-op for the legacy LangChain agent path."""
        return self

    def get_num_tokens(self, text: str) -> int:
        """Compatibility shim; heuristic chars/token estimate."""
        return int(len(text) / 3.5)

    # ------------------------------------------------------------------
    # Internal: request building
    # ------------------------------------------------------------------

    @staticmethod
    def _content_to_text(content: Any) -> str:
        """Flatten a string-or-block-list content field to plain text."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text" or "text" in block:
                        parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(p for p in parts if p)
        return "" if content is None else str(content)

    def _build_request(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> Dict[str, Any]:
        input_items: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")

            if role == "_multi_tool_results":
                for r in msg.get("results", []):
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": r.get("tool_call_id", ""),
                        "output": self._content_to_text(r.get("content", "")),
                    })
                continue

            if role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": self._content_to_text(content),
                })
                continue

            if role == "assistant":
                tool_calls = list(msg.get("tool_calls") or [])
                # Anthropic-style tool_use blocks in history → function_call
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                text = self._content_to_text(content)
                if text:
                    input_items.append({"role": "assistant", "content": text})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}"),
                    })
                continue

            # User (or unknown) role.  Anthropic-style tool_result blocks
            # become function_call_output items; image blocks become
            # input_image parts; text flattens to input_text.
            if isinstance(content, list):
                parts: List[Dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        if isinstance(block, str) and block:
                            parts.append({"type": "input_text", "text": block})
                        continue
                    btype = block.get("type")
                    if btype == "tool_result":
                        input_items.append({
                            "type": "function_call_output",
                            "call_id": block.get("tool_use_id", ""),
                            "output": self._content_to_text(block.get("content", "")),
                        })
                    elif btype == "image":
                        src = block.get("source", {})
                        if src.get("type") == "base64":
                            parts.append({
                                "type": "input_image",
                                "image_url": (
                                    f"data:{src.get('media_type', 'image/png')};"
                                    f"base64,{src.get('data', '')}"
                                ),
                            })
                    elif btype == "image_url":
                        # LangChain/OpenAI-chat style image block:
                        # {"type": "image_url", "image_url": {"url": "data:..."}}
                        # (or a bare string url).  Responses API wants a flat
                        # image_url string on an input_image part.
                        img = block.get("image_url", "")
                        url = img.get("url", "") if isinstance(img, dict) else img
                        if url:
                            parts.append({
                                "type": "input_image",
                                "image_url": url,
                            })
                    elif btype == "text" or "text" in block:
                        txt = block.get("text", "")
                        if txt:
                            parts.append({"type": "input_text", "text": txt})
                if parts:
                    input_items.append({"role": "user", "content": parts})
            elif content:
                input_items.append({"role": "user", "content": content})

        model_max = self.model_config.get("max_output_tokens", 16384)
        kwargs: Dict[str, Any] = {
            "model": self.model_id,
            "input": input_items,
            "stream": True,
            # Mantle is a stateless gateway; never ask it to persist responses.
            "store": False,
            "max_output_tokens": min(config.max_output_tokens, model_max),
        }
        if system_content:
            kwargs["instructions"] = system_content

        if config.temperature is not None:
            unsupported = set(self.model_config.get("unsupported_parameters", []) or [])
            if "temperature" not in unsupported:
                kwargs["temperature"] = config.temperature

        if tools and not config.suppress_tools:
            # Responses API tool schema is flat (not nested under "function").
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
            kwargs["tool_choice"] = "auto"

        if (
            config.thinking and config.thinking.enabled
            and self.model_config.get("supports_thinking")
        ):
            kwargs["reasoning"] = {
                "effort": _EFFORT_MAP.get(config.thinking.effort, "medium"),
                # Request summaries so reasoning streams back as
                # response.reasoning_summary_text.delta → ThinkingDelta.
                "summary": "auto",
            }

        return kwargs

    # ------------------------------------------------------------------
    # Internal: stream parsing
    # ------------------------------------------------------------------

    async def _do_stream(
        self, request_kwargs: Dict[str, Any]
    ) -> AsyncGenerator[StreamEvent, None]:
        active_tools: Dict[int, Dict[str, Any]] = {}
        latest_usage = None
        saw_function_call = False
        stop_reason = "end_turn"

        stream = await self.client.responses.create(**request_kwargs)

        async for event in stream:
            etype = getattr(event, "type", "")

            if etype == "response.output_text.delta":
                yield TextDelta(content=event.delta)

            elif etype in (
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            ):
                yield ThinkingDelta(content=event.delta)

            elif etype == "response.output_item.added":
                item = event.item
                if getattr(item, "type", "") == "function_call":
                    idx = getattr(event, "output_index", 0)
                    call_id = getattr(item, "call_id", "") or getattr(item, "id", "")
                    active_tools[idx] = {
                        "id": call_id,
                        "name": getattr(item, "name", ""),
                        "arguments": "",
                    }
                    saw_function_call = True
                    yield ToolUseStart(id=call_id, name=active_tools[idx]["name"], index=idx)

            elif etype == "response.function_call_arguments.delta":
                idx = getattr(event, "output_index", 0)
                if idx in active_tools:
                    active_tools[idx]["arguments"] += event.delta
                yield ToolUseInput(partial_json=event.delta, index=idx)

            elif etype == "response.output_item.done":
                item = event.item
                if getattr(item, "type", "") == "function_call":
                    idx = getattr(event, "output_index", 0)
                    entry = active_tools.pop(idx, None) or {}
                    args_str = getattr(item, "arguments", None) or entry.get("arguments", "")
                    try:
                        parsed = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    yield ToolUseEnd(
                        id=getattr(item, "call_id", "") or entry.get("id", ""),
                        name=getattr(item, "name", "") or entry.get("name", ""),
                        input=parsed,
                        index=idx,
                    )

            elif etype in ("response.completed", "response.incomplete"):
                resp = getattr(event, "response", None)
                usage = getattr(resp, "usage", None) if resp else None
                if usage:
                    latest_usage = usage
                if etype == "response.incomplete":
                    details = getattr(resp, "incomplete_details", None)
                    if getattr(details, "reason", "") == "max_output_tokens":
                        stop_reason = "max_tokens"

            elif etype in ("response.failed", "error"):
                resp = getattr(event, "response", None)
                err = getattr(resp, "error", None) if resp else None
                message = (
                    getattr(err, "message", None)
                    or getattr(event, "message", None)
                    or "Responses stream failed"
                )
                # Raise instead of yielding a non-retryable ErrorEvent so
                # that stream_response()'s existing retry/backoff wrapper
                # gets a chance to classify and retry this. Generic Mantle
                # backend faults (e.g. "The server had an error while
                # processing your request") are transient and should not
                # be treated the same as a genuinely fatal CONTEXT_LIMIT.
                raise RuntimeError(str(message))

        if saw_function_call and stop_reason == "end_turn":
            stop_reason = "tool_use"

        # Emit usage BEFORE StreamEnd so the orchestrator records it.
        if latest_usage is not None:
            u = latest_usage
            details = getattr(u, "input_tokens_details", None)
            yield UsageEvent(
                input_tokens=getattr(u, "input_tokens", 0),
                output_tokens=getattr(u, "output_tokens", 0),
                cache_read_tokens=getattr(details, "cached_tokens", 0) if details else 0,
            )
        yield StreamEnd(stop_reason=stop_reason)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_error(error_str: str) -> ErrorType:
        # See app/providers/error_scrub.py — a request_id containing "429"
        # or "rate" would otherwise force the THROTTLE branch below.
        error_str = scrub_request_id(error_str)
        lowered = error_str.lower()
        # This provider shares _AsyncSigV4Transport with BedrockMantleProvider,
        # so it is exposed to the same two SigV4 faults, and had a branch for
        # neither. Expired TOKEN is fatal (retrying cannot mint credentials);
        # expired SIGNATURE is retryable because signing happens per-attempt
        # inside the transport, so the next attempt stamps a fresh timestamp.
        # Both precede the "429"/"rate" ladder so neither can be shadowed.
        if ("expiredtoken" in lowered or "invalidclienttokenid" in lowered
                or "security token included in the request is expired" in lowered):
            return ErrorType.AUTH
        if ("signature expired" in lowered or "requestexpired" in lowered
                or "requesttimetooskewed" in lowered):
            return ErrorType.READ_TIMEOUT
        if "429" in error_str or "rate" in lowered or "too many" in lowered or "throttl" in lowered:
            return ErrorType.THROTTLE
        if "503" in error_str or "529" in error_str or "overloaded" in lowered:
            return ErrorType.OVERLOADED
        if "server had an error" in lowered or "sorry about that" in lowered:
            # Generic Mantle/Responses backend fault — transient upstream
            # failure, not a fatal request-shape problem. Treat as retryable.
            return ErrorType.OVERLOADED
        if "timeout" in lowered:
            return ErrorType.READ_TIMEOUT
        if "context" in lowered and ("long" in lowered or "large" in lowered or "limit" in lowered):
            return ErrorType.CONTEXT_LIMIT
        if "accessdenied" in lowered or "not authorized" in lowered or "403" in error_str:
            return ErrorType.AUTH
        return ErrorType.UNKNOWN
