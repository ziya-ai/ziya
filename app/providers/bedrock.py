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
    ThinkingBlock,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)
from app.utils.logging_utils import get_mode_aware_logger
from app.providers.bedrock_region_router import BedrockRegionRouter

from app.config.env_registry import ziya_env
logger = get_mode_aware_logger(__name__)


# Dedicated thread pools for Bedrock API calls.  Isolates blocking boto3
# operations (connect + stream reads) from the default asyncio executor
# so that tool execution, MCP communication, and health checks never
# stall waiting for a free thread.
#
# The two operations are deliberately kept in SEPARATE pools because their
# occupancy profiles are opposites:
#
#   READ  -- a worker is held for the ENTIRE lifetime of a stream.  The
#            reader parks in a blocking next() on the boto3 event iterator
#            and is not released while the model is thinking or while tool
#            calls run mid-stream.  Steady state is ~1 worker per ACTIVE
#            conversation.  Long-lived and mostly idle.
#   CONNECT -- short-lived, but it is the operation that gates a new turn
#            from starting at all.
#
# Sharing one pool couples them: once readers occupy every worker, connects
# land in the ThreadPoolExecutor work queue behind them.  That queue is
# unbounded and untimed, so asyncio.wait_for(..., timeout=connect_timeout)
# expires while the callable has not begun to run -- new turns stall
# indefinitely and the failure presents as a deadlock rather than a timeout.
# Splitting the pools makes that impossible: a connect can never wait on a
# parked reader.
#
# Workers in both pools are socket-blocked, not CPU-bound, so oversizing is
# cheap.  BEDROCK_THREAD_POOL_SIZE still sizes the read pool (the one that
# must exceed peak concurrent conversations); BEDROCK_CONNECT_POOL_SIZE
# sizes connects independently.
_BEDROCK_POOL_SIZE = int(os.environ.get("BEDROCK_THREAD_POOL_SIZE", "64"))
_bedrock_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_BEDROCK_POOL_SIZE, thread_name_prefix="bedrock-io"
)

# Connect-only pool.  Sized smaller than the read pool since occupancy is
# brief, but large enough that a burst of simultaneous new turns does not
# self-queue.
_BEDROCK_CONNECT_POOL_SIZE = int(
    os.environ.get("BEDROCK_CONNECT_POOL_SIZE", "32")
)
_bedrock_connect_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_BEDROCK_CONNECT_POOL_SIZE,
    thread_name_prefix="bedrock-connect",
)

class BedrockProvider(LLMProvider):
    """Streams Claude responses via AWS Bedrock."""

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        aws_profile: Optional[str] = None,
        region: str = "us-west-2",
    ):
        self.model_id = model_id
        self.model_config = model_config
        self._region = region
        self._aws_profile = aws_profile

        from app.providers.bedrock_client_cache import get_persistent_bedrock_client

        try:
            self.bedrock = get_persistent_bedrock_client(
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
            self.bedrock = boto3.client(
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

        # Serialize body once — reuse for both size check and API call.
        body_json = json.dumps(body)
        body_size = len(body_json)

        # Count images for diagnostics
        image_count = sum(
            1 for msg in messages
            if isinstance(msg.get("content"), list)
            for block in msg["content"]
            if isinstance(block, dict) and block.get("type") == "image"
        )

        # Scale connect timeout for large payloads.  Bedrock can take
        # several minutes to begin streaming when ingesting >200K tokens.
        if body_size > 800_000:  # ~200K tokens at ~4 chars/token
            connect_timeout = max(connect_timeout, 600)

        logger.debug(
            f"BedrockProvider: invoking {self.model_id} — "
            f"body={body_size/1_048_576:.1f}MB, images={image_count}, "
            f"messages={len(messages)}, timeout={connect_timeout}s"
        )
        call_start = time.time()

        for _attempt in range(1):  # Single attempt; loop kept for break-on-success
            try:
                api_params = {
                    "modelId": self.model_id,
                    "body": body_json,
                }
                # Run the synchronous boto3 call in a thread so it doesn't
                # block the event loop while waiting for the Bedrock API to
                # start streaming (can be slow for extended-context requests).
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        _bedrock_connect_executor,
                        lambda: self.bedrock.invoke_model_with_response_stream(**api_params),
                    ),
                    timeout=connect_timeout,
                )
                logger.debug(f"BedrockProvider: stream started in {time.time() - call_start:.1f}s")
                break
            except Exception as e:
                # asyncio.TimeoutError / builtin TimeoutError stringify to "",
                # which would mis-classify as UNKNOWN (non-retryable) and
                # surface an empty error message to the frontend. Build a
                # descriptive message so classification and UX both work.
                if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
                    error_str = (
                        f"Read timed out after {time.time() - call_start:.1f}s "
                        f"waiting for Bedrock to begin streaming "
                        f"(connect_timeout={connect_timeout}s)"
                    )
                else:
                    error_str = str(e) or f"{type(e).__name__}"

                # Augment the error with a remediation hint for the data-retention gate.
                if "data retention mode" in error_str and "not available for this model" in error_str:
                    error_str += (
                        " — This model requires the Bedrock account-level data retention mode "
                        "set to 'provider_data_share'. Restart Ziya with this model selected "
                        "to apply the setting automatically."
                    )

                classified = self._classify_error(error_str)
                logger.warning(
                    f"BedrockProvider: call failed after {time.time() - call_start:.1f}s — "
                    f"{classified.name}: {error_str[:500]}{'…[truncated]' if len(error_str) > 500 else ''}")

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
                                    _bedrock_connect_executor,
                                    lambda: self.bedrock.invoke_model_with_response_stream(**api_params),
                                ),
                                timeout=connect_timeout,
                            )
                            break
                        except Exception as e:
                            logger.debug("Extended context retry also failed: %s", e)
                            # fall through to ErrorEvent

                # Region failover: on throttle/overloaded/5xx, try an
                # alternate region before surfacing the error to the
                # orchestrator. This is a single failover attempt — not a
                # retry loop.
                #
                # SERVER_ERROR is included even though it is marked
                # non-retryable: "not retried" is correct for the SAME
                # endpoint (botocore has already exhausted its own retries
                # by this point) but a 5xx is a per-endpoint health signal,
                # not a property of the request. Excluding it meant an
                # InternalServerException killed the turn outright while two
                # other healthy regions sat unused.
                if (
                    classified in (ErrorType.THROTTLE, ErrorType.OVERLOADED,
                                   ErrorType.SERVER_ERROR)
                    and self._region_router.enabled
                ):
                    if classified is not ErrorType.SERVER_ERROR:
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
                                        _bedrock_connect_executor,
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
                logger.warning(
                    f"BedrockProvider: failed after {time.time() - call_start:.1f}s — "
                    f"{classified.name}: {error_str[:500]}{'…[truncated]' if len(error_str) > 500 else ''}"
                )
                return

        if response is None:
            yield ErrorEvent(message="No response after retries", error_type=ErrorType.UNKNOWN)
            return

        # Parse the boto3 stream into normalized events.
        # Track how many we actually produce: an empty 200 stream (zero
        # events, no exception) is the signature of a Bedrock-side empty
        # completion — observed intermittently on opus4.8 — which otherwise
        # surfaces as a silent "no output" bubble. Surface it with the
        # RequestId so it is visible and attributable to the service.
        _parsed_events = 0
        async for event in self._parse_stream(response, config):
            _parsed_events += 1
            yield event

        if _parsed_events == 0:
            _req_id = ""
            try:
                _req_id = (response.get("ResponseMetadata", {}) or {}).get("RequestId", "")
            except (AttributeError, TypeError, KeyError):
                pass  # Response metadata not available
            logger.warning(
                "BedrockProvider: empty event stream (0 events, HTTP 200) for "
                "model=%s region=%s RequestId=%s — Bedrock returned no content.",
                self.model_id, self._region, _req_id or "?",
            )
            yield ErrorEvent(
                message=f"Bedrock returned an empty response (RequestId={_req_id or 'unknown'}).",
                error_type=ErrorType.OVERLOADED,
                retryable=True,
            )
            # An empty stream is a failure, not a success — do not reward the
            # serving region (that would bias the router toward it).
            return

        # Successful completion — reward the region that served the request
        if self._region_router.enabled:
            self._region_router.report_success(self._region)

    def build_assistant_message(
        self,
        text: str,
        tool_uses: List[Dict[str, Any]],
        thinking_blocks: Optional[List[Dict[str, Any]]] = None,
        text_index: Optional[int] = None,
        text_segments: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        # Blocks are emitted in their ORIGINAL response order when ordering
        # metadata is present (thinking '_index', tool_use 'index',
        # text_index).  With adaptive/interleaved thinking the model emits
        # thinking blocks BETWEEN tool_use blocks, and the API rejects any
        # reordering of them in the latest assistant message ("thinking or
        # redacted_thinking blocks ... cannot be modified").  Without the
        # metadata the classic thinking-first order is kept.
        return {
            "role": "assistant",
            "content": self._ordered_assistant_content(
                text, tool_uses, thinking_blocks, text_index,
                text_segments=text_segments),
        }

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
        # Diagnostic / mitigation toggle: when set, send no cache_control
        # blocks at all. Used to isolate whether prompt caching is what an
        # opus4.8 endpoint chokes on (empty-200) for large multi-turn
        # histories that opus4.7 handles fine on the identical payload.
        if ziya_env("ZIYA_DISABLE_PROMPT_CACHE"):
            logger.info("🧪 PROMPT_CACHE: disabled via ZIYA_DISABLE_PROMPT_CACHE=1")
            return messages

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
            # Never the raw [-1]: a thinking block there is immutable, and
            # stamping it 400s the entire request rather than merely
            # costing a cache hit.  See LLMProvider._stamp_cache_control.
            if not self._stamp_cache_control(content):
                logger.debug(
                    "🧠 PROMPT_CACHE: boundary message carries only "
                    "reasoning blocks; skipping this breakpoint to keep "
                    "the request legal"
                )

        return messages

    def supports_feature(self, feature_name: str) -> bool:
        feature_map = {
            "thinking": self.model_config.get("supports_thinking", False),
            "adaptive_thinking": self.model_config.get("supports_adaptive_thinking", False),
            # Signed thinking blocks may be echoed back inside a tool chain so
            # reasoning survives the round-trip (Anthropic block format).
            "thinking_passback": (
                self.model_config.get("supports_thinking", False)
                or self.model_config.get("supports_adaptive_thinking", False)
            ),
            "extended_context": self.model_config.get("supports_extended_context", False),
            "cache_control": True,  # Bedrock Claude always supports caching
            "assistant_prefill": self.model_config.get("supports_assistant_prefill", True),
            # Anthropic-format tool_result blocks may carry image content
            # blocks; deliverable only if the model itself has vision.
            "image_tool_results": self.model_config.get("supports_vision", False),
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

    @staticmethod
    def _coalesce_same_role(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge consecutive same-role turns so the outgoing array always has
        strictly alternating roles, as the Anthropic Messages API requires.

        The conversation array is mutated in-loop by the tool executor
        (empty-after-tools nudges, deferred-feedback injection, max-tokens
        continuation). Any of those can append a turn whose role matches the
        previous turn — e.g. a nudge after a user[tool_result] turn yields
        user->user. opus4.8 answers a non-alternating array with an empty 200,
        so the recovery nudge becomes the cause of an empty-response loop.
        Repairing here, at the single choke point through which every outgoing
        array passes, makes that class of malformation impossible regardless of
        which upstream path produced it. The MSG_STRUCT diagnostic below then
        acts as a verifier: same_role_adjacent_idx should always read '-'.
        """
        merged: List[Dict[str, Any]] = []
        for msg in messages:
            if merged and merged[-1].get("role") == msg.get("role"):
                prev, cur = merged[-1].get("content"), msg.get("content")
                if isinstance(prev, str) and isinstance(cur, str):
                    merged[-1]["content"] = prev + "\n\n" + cur
                elif isinstance(prev, list) and isinstance(cur, list):
                    merged[-1]["content"] = prev + cur
                else:
                    pblocks = prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
                    cblocks = cur if isinstance(cur, list) else [{"type": "text", "text": cur}]
                    merged[-1]["content"] = pblocks + cblocks
                logger.info(
                    "🧹 COALESCE: Merged consecutive '%s' turns before send.",
                    msg.get("role"),
                )
            else:
                # Shallow-copy so a later in-place content merge never mutates
                # the caller's live conversation array.
                merged.append(dict(msg))
        return merged

    def _build_request_body(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ) -> Dict[str, Any]:
        # Coalesce BEFORE cache-control so cache markers are computed on the
        # final, strictly-alternating array.
        messages = self._coalesce_same_role(messages)
        body: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": config.max_output_tokens,
            "messages": self.prepare_cache_control(messages, config.iteration),
        }
        # ---- STRUCTURAL DIAGNOSTIC (temporary) ----------------------------
        # We have repeatedly inferred the outgoing message array and been
        # wrong. Log its actual shape so an empty-200 can be tied to a
        # concrete structural cause (dangling tool_use, role non-alternation).
        try:
            _msgs = body["messages"]
            _seq = []
            _tool_use_ids, _tool_result_ids = set(), set()
            for _m in _msgs:
                _role = _m.get("role")
                _c = _m.get("content")
                if isinstance(_c, str):
                    _btypes = ["str:%d" % len(_c)]
                elif isinstance(_c, list):
                    _btypes = []
                    for _b in _c:
                        if isinstance(_b, dict):
                            _bt = _b.get("type", "?")
                            _btypes.append(_bt)
                            if _bt == "tool_use":
                                _tool_use_ids.add(_b.get("id"))
                            elif _bt == "tool_result":
                                _tool_result_ids.add(_b.get("tool_use_id"))
                        else:
                            _btypes.append("nondict:%s" % type(_b).__name__)
                else:
                    _btypes = ["none" if _c is None else type(_c).__name__]
                _seq.append("%s[%s]" % (_role, ",".join(_btypes)))
            _dangling = _tool_use_ids - _tool_result_ids
            _orphan = _tool_result_ids - _tool_use_ids
            _alt = []
            for _i in range(1, len(_msgs)):
                if _msgs[_i].get("role") == _msgs[_i-1].get("role"):
                    _alt.append(_i)
            logger.info(
                "🧪 MSG_STRUCT n=%d seq=%s dangling_tool_use=%s orphan_tool_result=%s same_role_adjacent_idx=%s",
                len(_msgs), " | ".join(_seq), _dangling or "-", _orphan or "-", _alt or "-",
            )
        except Exception as _diag_e:
            logger.warning("🧪 MSG_STRUCT diagnostic failed: %s", _diag_e)
        # ---- END DIAGNOSTIC ----------------------------------------------

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

        # Temperature — defense-in-depth: some models (e.g. Claude Opus 4.7)
        # reject `temperature` with a 400 ValidationException. The ziya_bedrock
        # wrapper strips it at construction time, but any caller that builds a
        # ProviderConfig directly could re-introduce it. Filter here against
        # the model's declared unsupported_parameters as a final safeguard.
        if config.temperature is not None:
            unsupported = set(self.model_config.get("unsupported_parameters", []) or [])
            if "temperature" not in unsupported:
                body["temperature"] = config.temperature

        # Thinking configuration
        if config.thinking:
            self._apply_thinking(body, config.thinking)

        # Tools
        if tools and not config.suppress_tools:
            body["tools"] = tools
            body["tool_choice"] = {"type": "auto"}

        # Diagnostic: dump the session-dependent request components to disk so
        # an offline probe (scripts/bisect_refusal.py) can reproduce the exact
        # payload. The system prompt and tool schemas are assembled at runtime
        # from MCP server state and cannot be reconstructed from a stored
        # conversation, so a refusal that depends on them is otherwise
        # impossible to bisect. Writes once per process; set the env var to a
        # directory path.
        _dump_dir = ziya_env("ZIYA_DUMP_REQUEST_PARTS")
        if _dump_dir and not getattr(BedrockProvider, "_parts_dumped", False):
            try:
                BedrockProvider._parts_dumped = True
                _dd = os.path.expanduser(str(_dump_dir))
                os.makedirs(_dd, exist_ok=True)
                _sys = body.get("system")
                if _sys:
                    _text = "".join(
                        b.get("text", "") for b in _sys
                    ) if isinstance(_sys, list) else str(_sys)
                    with open(os.path.join(_dd, "system.txt"), "w",
                              encoding="utf-8") as _f:
                        _f.write(_text)
                if body.get("tools"):
                    with open(os.path.join(_dd, "tools.json"), "w",
                              encoding="utf-8") as _f:
                        json.dump(body["tools"], _f, indent=2)
                with open(os.path.join(_dd, "params.json"), "w",
                          encoding="utf-8") as _f:
                    json.dump({
                        "model_id": self.model_id,
                        "max_tokens": body.get("max_tokens"),
                        "thinking": body.get("thinking"),
                        "output_config": body.get("output_config"),
                        "anthropic_beta": body.get("anthropic_beta"),
                        "tool_choice": body.get("tool_choice"),
                        "message_count": len(body.get("messages") or []),
                    }, _f, indent=2)
                logger.info("🗒️ REQUEST_PARTS: dumped to %s", _dd)
            except Exception as _de:
                logger.warning("🗒️ REQUEST_PARTS dump failed: %s", _de)

        # Diagnostic: size census of every top-level body component. The
        # tool schemas are the one large component absent from all
        # ESTIMATE_ACCURACY buckets, so their true cost is unmeasured.
        try:
            _sizes = {k: len(json.dumps(v)) for k, v in body.items()}
            _tools_n = len(body.get("tools") or [])
            _biggest = sorted(
                ((len(json.dumps(t)), t.get("name", "?")) for t in (body.get("tools") or [])),
                reverse=True,
            )[:5]
            logger.info(
                "🧮 BODY_CENSUS total=%d parts=%s tools_n=%d biggest_tools=%s "
                "thinking=%s output_config=%s max_tokens=%s",
                sum(_sizes.values()), _sizes, _tools_n, _biggest,
                body.get("thinking"), body.get("output_config"),
                body.get("max_tokens"),
            )
        except Exception as _ce:
            logger.warning("🧮 BODY_CENSUS failed: %s", _ce)

        return body

    def _apply_thinking(self, body: Dict[str, Any], thinking: "ThinkingConfig") -> None:
        from app.providers.base import ThinkingConfig  # avoid circular at module level

        if thinking.mode == "adaptive":
            # display defaults to "omitted" on Opus 4.7/4.8/5, Sonnet 5,
            # Fable 5 and Mythos 5 (Anthropic's adaptive-thinking docs), and
            # with display=omitted the API emits NO thinking_delta events at
            # all -- only the closing signature_delta.  The reasoning is
            # still billed via output_tokens_details.thinking_tokens, so
            # leaving this unset means paying for reasoning that can never
            # be shown: one measured opus-5 response billed 680 thinking
            # tokens (37% of its output) and delivered zero characters.
            #
            # Set unconditionally rather than per-model: the older models
            # (Sonnet 4.6, Opus 4.6, Haiku 4.5) already default to
            # "summarized", so passing it explicitly is a no-op for them
            # and there is no list to keep in sync as models are added.
            body["thinking"] = {"type": "adaptive", "display": "summarized"}
            supported = self.model_config.get("supported_efforts", ["low", "medium", "high", "max"])
            effort = thinking.effort if thinking.effort in supported else self.model_config.get("thinking_effort_default", "medium")
            if effort != thinking.effort:
                logger.warning("Effort '%s' not supported by this model, falling back to '%s'", thinking.effort, effort)
            if effort in supported:
                body.setdefault("output_config", {})["effort"] = effort
                if self.model_config.get("effort_beta_required", True):
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

        # Diagnostic: chunk types seen, and any block/delta type the parser
        # does not recognize. Without this an unhandled block type is
        # indistinguishable from an empty completion.
        _chunk_types: Dict[str, int] = {}

        # Thinking-visibility census.  ``output_tokens_details.thinking_tokens``
        # is BILLED reasoning; ``_thinking_chars`` is what actually streamed
        # back as thinking_delta content.  Nothing else in the codebase reads
        # the former, so a model that bills for reasoning it does not emit is
        # invisible — the two must be compared to tell "we are not displaying
        # thinking" from "the API never sent it".
        _thinking_deltas = 0
        _thinking_chars = 0
        _text_deltas = 0
        _text_chars = 0
        _tool_json_chars = 0
        _reported_thinking_tokens: Optional[int] = None
        _reported_output_tokens: Optional[int] = None

        # The true stop_reason arrives on message_delta, which this parser
        # never handled — Bedrock's message_stop carries only metrics.
        _delta_stop_reason: Optional[str] = None

        stream_iter = iter(stream_body)
        in_thinking_block = False

        # Completed thinking blocks for reasoning-continuity passback, keyed by
        # content-block index.  A signature_delta is what closes each one; a
        # block without a signature cannot be echoed back and is dropped.
        _thinking_blocks: Dict[int, Dict[str, Any]] = {}

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

            except asyncio.CancelledError:
                # Ctrl+C or task cancellation — close the boto3 stream so
                # the thread blocked in next(stream_iter) gets unblocked.
                # Without this, shield() prevents cancellation from reaching
                # the run_in_executor future and the thread sits in a
                # blocking network read forever.
                try:
                    stream_body.close()
                except Exception:  # noqa: BLE001 — best-effort during cancellation
                    pass  # Stream body may already be closed
                raise

            except asyncio.TimeoutError:
                # Outer safety net — should not be reached with the inner handling,
                # but guard against edge cases.
                continue

            if event is None:
                break  # stream exhausted

            # Got data — reset silence counter
            silence_elapsed = 0.0

            if "chunk" not in event:
                # boto3 delivers in-stream errors as separate event types
                # (NOT 'chunk'), keyed by the exception name. The HTTP
                # envelope still returns 200, so these are invisible unless
                # we inspect the event. Silently skipping them produced an
                # empty stream (events_seen=0, stop_reason=None) that
                # surfaced as a phantom "no output" completion — seen
                # intermittently on opus4.8.
                _exc_keys = {
                    "internalServerException": ErrorType.SERVER_ERROR,
                    "modelStreamErrorException": ErrorType.UNKNOWN,
                    "throttlingException": ErrorType.THROTTLE,
                    "modelTimeoutException": ErrorType.READ_TIMEOUT,
                    "serviceUnavailableException": ErrorType.OVERLOADED,
                    "validationException": ErrorType.UNKNOWN,
                }
                _hit = next((k for k in _exc_keys if k in event), None)
                if _hit is not None:
                    _payload = event.get(_hit) or {}
                    _msg = _payload.get("message", _hit) if isinstance(_payload, dict) else str(_payload)
                    _etype = _exc_keys[_hit]
                    logger.warning(
                        "Bedrock in-stream exception event '%s': %s", _hit, _msg
                    )
                    yield ErrorEvent(
                        message=f"{_hit}: {_msg}",
                        error_type=_etype,
                        retryable=_etype in (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED),
                    )
                    return
                # Genuinely unrecognised non-chunk event — log and skip.
                logger.debug("Skipping non-chunk stream event: keys=%s", list(event.keys()))
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
                    # Reasoning tokens billed inside output_tokens.  Carried
                    # on the EXISTING event rather than a second one:
                    # _handle_usage_event runs calibration unguarded on
                    # iteration 0, so an extra UsageEvent would record the
                    # same iteration twice.  Live-verified safe -- Bedrock
                    # sends message_delta (which carries thinking_tokens)
                    # before message_stop (which carries these metrics), so
                    # the value is already known here.
                    thinking_tokens=_reported_thinking_tokens or 0,
                )

            chunk_type = chunk.get("type", "")
            _chunk_types[chunk_type or "<none>"] = _chunk_types.get(chunk_type or "<none>", 0) + 1

            if chunk_type == "content_block_start":
                cb = chunk.get("content_block", {})
                idx = chunk.get("index", 0)
                block_type = cb.get("type", "")
                if block_type == "thinking":
                    in_thinking_block = True
                    _thinking_blocks[idx] = {
                        "type": "thinking", "thinking": "", "signature": None,
                    }
                elif block_type == "redacted_thinking":
                    # Opaque encrypted reasoning.  Captured rather than ignored
                    # because passback is all-or-nothing: echoing the readable
                    # thinking blocks while silently dropping a redacted one
                    # reorders the assistant turn and the API rejects it.  The
                    # payload arrives whole on this event, not via deltas.
                    _thinking_blocks[idx] = {
                        "type": "redacted_thinking", "data": cb.get("data", ""),
                    }
                elif block_type == "text":
                    # Known and deliberately inert: a text block needs no
                    # action, its content arrives as text_delta.  Whitelisted
                    # explicitly rather than left to fall through to the
                    # warning below: an adaptive thinking response opens a
                    # text block on EVERY iteration, so the warning fired
                    # several times per turn and taught readers to disregard
                    # the channel that is supposed to flag genuinely unknown
                    # block types.
                    logger.debug(
                        "content_block_start type=%r index=%s (no-op)",
                        block_type, idx,
                    )
                elif block_type == "tool_use":
                    tool_id = cb.get("id", "")
                    tool_name = cb.get("name", "")
                    active_tools[tool_id] = {
                        "name": tool_name,
                        "partial_json": "",
                        "index": idx,
                    }
                    yield ToolUseStart(id=tool_id, name=tool_name, index=idx)
                elif block_type:
                    logger.warning(
                        "🔍 UNHANDLED content_block_start type=%r index=%s keys=%s",
                        block_type, idx, sorted(cb.keys()),
                    )

            elif chunk_type == "content_block_delta":
                delta = chunk.get("delta", {})
                idx = chunk.get("index", 0)
                delta_type = delta.get("type", "")

                if delta_type == "text_delta":
                    _text = delta.get("text", "")
                    _text_deltas += 1
                    _text_chars += len(_text)
                    yield TextDelta(content=_text, index=idx)

                elif delta_type == "thinking_delta":
                    _thinking = delta.get("thinking", "")
                    _thinking_deltas += 1
                    _thinking_chars += len(_thinking)
                    if idx in _thinking_blocks:
                        _thinking_blocks[idx]["thinking"] += _thinking
                    yield ThinkingDelta(content=_thinking)

                elif delta_type == "signature_delta":
                    # Closes a thinking block with the cryptographic signature
                    # the API requires to verify the block when it is echoed
                    # back.  Retained so the assistant turn can carry its own
                    # reasoning across a tool round-trip: without it the model
                    # re-derives its plan from scratch every iteration, paying
                    # for the same reasoning repeatedly.
                    _sig = delta.get("signature", "")
                    if idx in _thinking_blocks and _sig:
                        _thinking_blocks[idx]["signature"] = _sig
                    else:
                        logger.debug(
                            "signature_delta index=%s has no open thinking "
                            "block (sig_present=%s)", idx, bool(_sig),
                        )

                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    _tool_json_chars += len(partial)
                    # Accumulate for ToolUseEnd
                    for tid, tdata in active_tools.items():
                        if tdata["index"] == idx:
                            tdata["partial_json"] += partial
                            break
                    yield ToolUseInput(partial_json=partial, index=idx)

                else:
                    logger.warning(
                        "🔍 UNHANDLED content_block_delta type=%r index=%s keys=%s",
                        delta_type, idx, sorted(delta.keys()),
                    )

            elif chunk_type == "content_block_stop":
                idx = chunk.get("index", 0)
                # If a thinking block just finished, clear the flag
                if in_thinking_block:
                    in_thinking_block = False
                # Emit the completed thinking block for passback.  An
                # unsigned readable block is surfaced with signature=None
                # (not silently dropped) so the assembler can skip the
                # whole turn's passback rather than send a gapped set.
                _tb = _thinking_blocks.pop(idx, None)
                if _tb is not None:
                    if _tb["type"] == "redacted_thinking":
                        yield ThinkingBlock(
                            block_type="redacted_thinking",
                            data=_tb.get("data", ""), index=idx,
                        )
                    elif _tb.get("signature"):
                        yield ThinkingBlock(
                            content=_tb["thinking"],
                            signature=_tb["signature"], index=idx,
                        )
                    else:
                        # Unsigned readable block: surfaced with
                        # signature=None instead of silently dropped
                        # — a silent drop gapped the turn and shifted
                        # every later block out of its original
                        # position, the same "cannot be modified"
                        # 400.  The assembler skips ALL thinking
                        # passback for the turn.
                        logger.warning(
                            "🧠 THINKING_PASSBACK: thinking block "
                            "index=%s closed without a signature "
                            "(len=%d) — thinking passback will be "
                            "skipped for this turn",
                            idx, len(_tb.get("thinking", "")),
                        )
                        yield ThinkingBlock(
                            content=_tb.get("thinking", ""),
                            signature=None, index=idx,
                        )
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

            elif chunk_type == "message_delta":
                _d = chunk.get("delta", {}) or {}
                if _d.get("stop_reason"):
                    _delta_stop_reason = _d.get("stop_reason")
                _u = chunk.get("usage", {}) or {}
                if "output_tokens" in _u:
                    _reported_output_tokens = _u.get("output_tokens")
                _otd = _u.get("output_tokens_details", {}) or {}
                if "thinking_tokens" in _otd:
                    _reported_thinking_tokens = _otd.get("thinking_tokens")
                logger.info("🔍 MESSAGE_DELTA raw=%s", json.dumps(chunk)[:400])

            elif chunk_type == "message_stop":
                stop_reason = (
                    _delta_stop_reason
                    or chunk.get("stop_reason")
                    or chunk.get("amazon-bedrock-stop-reason")
                    or "end_turn"
                )
                if not any(k.startswith("content_block") for k in _chunk_types):
                    logger.warning(
                        "🔍 NO_CONTENT_BLOCKS model=%s stop_reason=%r chunk_types=%s raw_message_stop=%s",
                        self.model_id, stop_reason, _chunk_types, json.dumps(chunk)[:600],
                    )
                # Thinking-visibility census.  Answers a question no other
                # log line can: whether reasoning we are BILLED for was
                # actually streamed to us.  ``billed`` is the API's own
                # output_tokens_details.thinking_tokens, which nothing else
                # in the codebase reads — it reached a human only
                # incidentally, inside the raw MESSAGE_DELTA dump.  A large
                # billed figure with streamed=0 means the API summarised or
                # withheld the reasoning, NOT that Ziya dropped it; the two
                # diagnoses call for opposite fixes, so they must be
                # distinguishable at a glance.
                #
                # Gated on either signal being present, so an ordinary
                # non-thinking turn gains no log noise — the same mistake
                # the UNHANDLED warnings above were making.
                if _reported_thinking_tokens or _thinking_deltas:
                    _billed = _reported_thinking_tokens
                    # Self-calibrating chars/token, derived from THIS
                    # response rather than a constant.  Live measurement on
                    # sonnet4.6 gave 1.58 / 2.27 / 3.63 chars-per-token
                    # across three requests, so any fixed divisor is wrong
                    # most of the time: 3.5 scored fully-delivered streams
                    # at 0.25-0.72 and would have been read as the API
                    # withholding reasoning it had in fact sent -- the exact
                    # false alarm this census exists to rule out.
                    #
                    # Visible tokens = out_tok - billed_thinking, and their
                    # char count is known, so the ratio falls out with no
                    # guessing.  Gated because quantization dominates on
                    # short samples (a 19-char thinking block cannot be
                    # measured to 2 decimal places); below the gate the
                    # ratio reads n/a rather than a number to over-trust.
                    _ratio = "n/a"
                    _est = 0
                    _vis_chars = _text_chars + _tool_json_chars
                    _vis_tok = (
                        (_reported_output_tokens or 0) - (_billed or 0)
                    )
                    if _billed and not _thinking_chars:
                        # Nothing streamed at all: exact, not an estimate.
                        # Deliberately ahead of the small-sample gate --
                        # gating the definitive withheld case would hide the
                        # one signal the census exists to surface.
                        _ratio = "0.00"
                    elif _vis_tok > 0 and _vis_chars > 0:
                        _cpt = _vis_chars / _vis_tok
                        _est = round(_thinking_chars / _cpt) if _cpt else 0
                        if _billed and _thinking_chars >= 200 and _vis_tok >= 50:
                            _ratio = f"{_est / _billed:.2f}"
                        elif _billed:
                            _ratio = "n/a(small)"
                    logger.info(
                        "🧠 THINKING_CENSUS model=%s billed_thinking_tok=%s "
                        "streamed_deltas=%d streamed_chars=%d est_tok=%d "
                        "est/billed=%s out_tok=%s text_deltas=%d text_chars=%d "
                        "visible=%s",
                        self.model_id, _billed if _billed is not None else "?",
                        _thinking_deltas, _thinking_chars, _est, _ratio,
                        _reported_output_tokens
                        if _reported_output_tokens is not None else "?",
                        _text_deltas, _text_chars,
                        "yes" if _thinking_deltas else "NO",
                    )
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
        # Credential-retrieval failures from a configured credential_process
        # (e.g. ada/midway). Botocore's CredentialRetrievalError formats as
        # "Error when retrieving credentials from {provider}: {error_msg}" --
        # the exception CLASS NAME never appears in str(exc), only the lower-
        # case word "credentials", so match on that phrase instead of the
        # (never-present) class name. A failure to even reach the auth
        # endpoint (DNS/network/client-init issues, including ada's own
        # "failed to initialize iibs client: Get \"\": unsupported protocol
        # scheme \"\"" when a corp-network redirect returns empty) is a
        # transient network problem, not an expired/invalid token. Route
        # those to READ_TIMEOUT (retryable) instead of leaving them
        # unclassified as UNKNOWN/non-retryable with the raw Go error text.
        _is_cred_retrieval = "retrieving credentials" in lowered or "credentialretrievalerror" in lowered
        # "failed to initialize iibs client" is ada's generic wrapper and is
        # NOT itself proof of a transient network fault -- it also wraps a
        # genuine expired/invalid Midway session, where the IDP redirect
        # completes (reaches the server) but returns an explicit 401 and
        # tells the user to run mwinit. That case must classify as AUTH so
        # the concise credential-help template is shown instead of silently
        # retrying a login that will never succeed without user action.
        _is_expired_session = _is_cred_retrieval and (
            "did not redirect. status code: 401" in lowered or "you may need to authenticate" in lowered
        )
        if _is_cred_retrieval and (
                any(ind in lowered for ind in (
                    "no such host", "dial tcp", "i/o timeout", "context deadline exceeded",
                    "temporary failure in name resolution", "name or service not known",
                    "connection refused", "network is unreachable", "connection reset",
                    "unsupported protocol scheme"))
                or ("failed to initialize iibs client" in lowered and not _is_expired_session)):
            return ErrorType.READ_TIMEOUT
        if _is_cred_retrieval:
            return ErrorType.AUTH
        if any(s in error_str for s in ("Read timed out", "ReadTimeoutError")) or "timeout" in lowered:
            return ErrorType.READ_TIMEOUT
        # Connection-quality drops (flaky/unreliable networks): TLS socket severed
        # mid-handshake or mid-stream. Transient and retriable, NOT auth failures.
        if any(s in error_str for s in (
            "UNEXPECTED_EOF_WHILE_READING", "EOF occurred in violation of protocol",
            "SSL validation failed", "SSLError", "Connection reset by peer",
            "ConnectionResetError", "Connection aborted", "Connection broken",
            "EndpointConnectionError", "ConnectionClosedError")):
            return ErrorType.READ_TIMEOUT
        # httpx-style mid-stream drops (RemoteProtocolError / ChunkedEncodingError).
        # Matched case-insensitively so phrasing variants are covered. bedrock
        # normally rides botocore/urllib3 (caught above), but a shared/wrapped
        # httpx transport can surface these; align with AnthropicDirectProvider
        # so the same transient drop is retriable on either path.
        # "response ended prematurely" is the urllib3 spelling of the SAME
        # fault (urllib3.exceptions.ProtocolError, response.py:1344 — socket
        # closed mid-body). It was absent here, so an identical mid-stream
        # drop was retriable over httpx but fatal over urllib3; observed
        # killing a 2.5h task card at iteration 5 of 20 with 4 passes banked.
        if ("peer closed connection" in lowered or "incomplete chunked read" in lowered
                or "remoteprotocolerror" in lowered or "server disconnected" in lowered
                or "ended prematurely" in lowered or "protocolerror" in lowered):
            return ErrorType.READ_TIMEOUT
        if "InternalServerException" in error_str:
            return ErrorType.SERVER_ERROR
        if "overloaded" in lowered or "529" in error_str or "ServiceUnavailableException" in error_str:
            return ErrorType.OVERLOADED
        return ErrorType.UNKNOWN
