"""
Nova Bedrock LLM Provider — streams responses via the Converse API.

Nova models (Micro, Lite, Pro, Premier) use the Bedrock Converse API
rather than the Anthropic-format invoke_model API that Claude uses.
Key differences:
  - No anthropic_version or top-level max_tokens
  - Uses inferenceConfig.maxTokens
  - Message content must be arrays of content blocks, not plain strings
  - System prompt is an array of {text: ...} blocks
  - Tool definitions use toolSpec format
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
    StreamEnd, StreamEvent, TextDelta, ToolUseEnd, ToolUseInput,
    ToolUseStart, UsageEvent,
)
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

_POOL_SIZE = int(os.environ.get("BEDROCK_THREAD_POOL_SIZE", "8"))
_nova_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_POOL_SIZE, thread_name_prefix="nova-io"
)


class NovaBedrockProvider(LLMProvider):
    """Streams Nova model responses via the Bedrock Converse API."""

    def __init__(self, model_id: str, model_config: Dict[str, Any],
                 aws_profile: str = "ziya", region: str = "us-west-2"):
        self.model_id = model_id
        self.model_config = model_config
        self._region = region
        self._aws_profile = aws_profile

        from app.providers.bedrock_client_cache import get_persistent_bedrock_client
        try:
            self.bedrock = get_persistent_bedrock_client(
                aws_profile=aws_profile, region=region,
                model_id=model_id, model_config=model_config,
            )
        except Exception as e:
            logger.warning(f"NovaBedrockProvider: persistent client failed ({e}), falling back to boto3")
            import boto3
            from botocore.config import Config as BotoConfig
            self.bedrock = boto3.client(
                "bedrock-runtime", region_name=region,
                config=BotoConfig(max_pool_connections=25,
                                  retries={"max_attempts": 2, "mode": "adaptive"}),
            )

    # -- LLMProvider interface ---------------------------------------------

    async def stream_response(self, messages, system_content, tools, config):
        params = self._build_converse_params(messages, system_content, tools, config)
        timeout = int(os.environ.get("BEDROCK_CONNECT_TIMEOUT", "180"))
        logger.debug(f"NovaBedrockProvider: converse_stream {self.model_id}, msgs={len(messages)}")
        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    _nova_executor, lambda: self.bedrock.converse_stream(**params)),
                timeout=timeout)
        except Exception as e:
            ct = self._classify_error(str(e))
            logger.warning(f"NovaBedrockProvider: failed {time.time()-t0:.1f}s — {ct.name}: {str(e)[:200]}")
            yield ErrorEvent(message=str(e), error_type=ct,
                             retryable=ct in (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED))
            return
        async for ev in self._parse_converse_stream(response, config):
            yield ev

    def build_assistant_message(self, text, tool_uses):
        blocks = []
        if text.strip():
            blocks.append({"text": text.rstrip()})
        for tu in tool_uses:
            name = tu["name"][4:] if tu["name"].startswith("mcp_") else tu["name"]
            blocks.append({"toolUse": {"toolUseId": tu["id"], "name": name,
                                       "input": tu.get("input", {})}})
        return {"role": "assistant", "content": blocks}

    def build_tool_result_message(self, tool_results):
        blocks = []
        for tr in tool_results:
            blocks.append({"toolResult": {"toolUseId": tr["tool_use_id"],
                                          "content": [{"text": tr["content"]}]}})
        return {"role": "user", "content": blocks}

    def supports_feature(self, feature_name):
        m = {"thinking": self.model_config.get("supports_thinking", False),
             "assistant_prefill": self.model_config.get("supports_assistant_prefill", False)}
        return bool(m.get(feature_name, False))

    @property
    def provider_name(self):
        return "nova_bedrock"

    # -- Request building --------------------------------------------------

    def _build_converse_params(self, messages, system_content, tools, config):
        formatted = self._format_messages(messages)
        # Nova models have hard output token limits that differ from Claude.
        # The STE may pass a value inherited from a previously active Claude
        # model (via ZIYA_MAX_OUTPUT_TOKENS env var), so cap to the model's
        # configured max_output_tokens to avoid ValidationException.
        model_max = self.model_config.get("max_output_tokens", 5000)
        effective = min(config.max_output_tokens, model_max)
        inf = {"maxTokens": effective}
        if config.temperature is not None:
            inf["temperature"] = config.temperature
        params = {"modelId": self.model_id, "messages": formatted,
                  "inferenceConfig": inf}
        if system_content:
            if isinstance(system_content, str):
                params["system"] = [{"text": system_content}]
            elif isinstance(system_content, list):
                params["system"] = [{"text": b} if isinstance(b, str) else b
                                    for b in system_content]
        # Some models (e.g. DeepSeek R1) don't support tool use in streaming.
        # Check native_function_calling from model config before sending tools.
        model_supports_tools = self.model_config.get("native_function_calling", True)
        if tools and not config.suppress_tools and model_supports_tools:
            nt = self._convert_tools(tools)
            if nt:
                params["toolConfig"] = {"tools": nt, "toolChoice": {"auto": {}}}
        return params

    def _format_messages(self, messages):
        out = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                fc = [{"text": content}] if content else [{"text": " "}]
            elif isinstance(content, list):
                fc = self._normalize_content_blocks(content)
            else:
                fc = [{"text": str(content)}]
            out.append({"role": role, "content": fc})
        return out

    @staticmethod
    def _normalize_content_blocks(blocks):
        """Anthropic content blocks -> Converse content blocks."""
        result = []
        for b in blocks:
            if isinstance(b, str):
                result.append({"text": b})
            elif isinstance(b, dict):
                bt = b.get("type", "")
                if bt == "text":
                    result.append({"text": b.get("text", "")})
                elif bt == "tool_use":
                    result.append({"toolUse": {"toolUseId": b.get("id", ""),
                                               "name": b.get("name", ""),
                                               "input": b.get("input", {})}})
                elif bt == "tool_result":
                    result.append({"toolResult": {"toolUseId": b.get("tool_use_id", ""),
                                                  "content": [{"text": b.get("content", "")}]}})
                elif bt == "image":
                    src = b.get("source", {})
                    if src.get("type") == "base64":
                        import base64 as b64
                        mime = src.get("media_type", "image/png")
                        result.append({"image": {"format": mime.split("/")[-1],
                                                 "source": {"bytes": b64.b64decode(src.get("data", ""))}}})
                    else:
                        result.append({"text": "[image]"})
                elif "text" in b:
                    result.append(b)
                elif "toolUse" in b or "toolResult" in b:
                    result.append(b)
                else:
                    result.append({"text": str(b)})
            else:
                result.append({"text": str(b)})
        return result or [{"text": " "}]

    @staticmethod
    def _convert_tools(tools):
        """Anthropic tool defs -> Converse API toolSpec format."""
        out = []
        for t in tools:
            desc = t.get("description", "")
            if len(desc) > 4096:
                desc = desc[:4090] + " ..."
            out.append({"toolSpec": {
                "name": t.get("name", ""),
                "description": desc,
                "inputSchema": {"json": t.get("input_schema",
                                               {"type": "object", "properties": {}})},
            }})
        return out

    # -- Stream parsing ----------------------------------------------------

    async def _parse_converse_stream(self, response, config):
        stream = response.get("stream", [])
        poll = int(os.environ.get("STREAM_STALL_TIMEOUT", "120"))
        silence = 0.0
        active_tools: Dict[str, Dict[str, Any]] = {}
        it = iter(stream)
        pending = None

        while True:
            if pending is None:
                def _nxt(i=it):
                    try:
                        return next(i)
                    except StopIteration:
                        return None
                pending = asyncio.ensure_future(
                    asyncio.get_event_loop().run_in_executor(_nova_executor, _nxt))
            try:
                try:
                    ev = await asyncio.wait_for(asyncio.shield(pending), timeout=poll)
                except asyncio.TimeoutError:
                    silence += poll
                    if silence >= poll:
                        pending.cancel(); pending = None
                        yield ErrorEvent(message=f"Stream stalled {int(silence)}s",
                                         error_type=ErrorType.READ_TIMEOUT, retryable=True)
                        return
                    yield ProcessingEvent(elapsed_seconds=silence, phase="processing")
                    continue
                pending = None
            except asyncio.CancelledError:
                if pending:
                    pending.cancel()
                raise

            if ev is None:
                break
            silence = 0.0

            if "contentBlockStart" in ev:
                cb = ev["contentBlockStart"]
                idx = cb.get("contentBlockIndex", 0)
                start = cb.get("start", {})
                if "toolUse" in start:
                    tid = start["toolUse"].get("toolUseId", "")
                    tn = start["toolUse"].get("name", "")
                    active_tools[tid] = {"name": tn, "pj": "", "idx": idx}
                    yield ToolUseStart(id=tid, name=tn, index=idx)

            elif "contentBlockDelta" in ev:
                db = ev["contentBlockDelta"]
                idx = db.get("contentBlockIndex", 0)
                d = db.get("delta", {})
                if "text" in d:
                    yield TextDelta(content=d["text"])
                elif "toolUse" in d:
                    p = d["toolUse"].get("input", "")
                    for tid, td in active_tools.items():
                        if td["idx"] == idx:
                            td["pj"] += p
                            break
                    yield ToolUseInput(partial_json=p, index=idx)

            elif "contentBlockStop" in ev:
                idx = ev["contentBlockStop"].get("contentBlockIndex", 0)
                fid = None
                for tid, td in active_tools.items():
                    if td["idx"] == idx:
                        fid = tid
                        break
                if fid:
                    td = active_tools.pop(fid)
                    try:
                        pi = json.loads(td["pj"]) if td["pj"] else {}
                    except json.JSONDecodeError:
                        pi = {}
                    yield ToolUseEnd(id=fid, name=td["name"], input=pi, index=idx)

            elif "messageStop" in ev:
                yield StreamEnd(stop_reason=ev["messageStop"].get("stopReason", "end_turn"))

            elif "metadata" in ev:
                u = ev["metadata"].get("usage", {})
                if u:
                    yield UsageEvent(input_tokens=u.get("inputTokens", 0),
                                     output_tokens=u.get("outputTokens", 0))

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _classify_error(s):
        lo = s.lower()
        if any(x in s for x in ("ThrottlingException", "Too many tokens", "Too many requests")) or "rate limit" in lo:
            return ErrorType.THROTTLE
        if any(x in s for x in ("Input is too long", "too large", "prompt is too long")):
            return ErrorType.CONTEXT_LIMIT
        if any(x in s for x in ("Read timed out", "ReadTimeoutError")) or "timeout" in lo:
            return ErrorType.READ_TIMEOUT
        if "overloaded" in lo or "529" in s or "ServiceUnavailableException" in s:
            return ErrorType.OVERLOADED
        return ErrorType.UNKNOWN
