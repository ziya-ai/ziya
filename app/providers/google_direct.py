"""
Google Direct LLM Provider — streams responses via the google-genai SDK.

Translates the normalized LLMProvider interface (OpenAI-format messages,
StreamEvent output) into Google Gemini's generate_content_stream API.
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


# JSON Schema keys that Google Gemini's FunctionDeclaration rejects outright.
# Some (e.g. additionalProperties) are accepted by the Pydantic Schema model
# but rejected by the REST API, producing a 400 with Unknown name errors.
# Others (e.g. exclusiveMinimum) are rejected by Pydantic itself.
# Stripping must happen at every nesting depth.
_GEMINI_STRIP_KEYS = frozenset({
    "additionalProperties", "additional_properties",
    "exclusiveMinimum", "exclusiveMaximum",
    "examples",  # Google accepts singular 'example' only
    "const", "patternProperties", "not", "oneOf", "allOf",
    "$schema", "$defs", "$ref", "$comment",
    "readOnly", "writeOnly",
    "contentEncoding", "contentMediaType",
    "dependencies", "dependentSchemas", "dependentRequired",
    "if", "then", "else",
    "uniqueItems", "propertyNames",
    "unevaluatedProperties", "unevaluatedItems",
    "title",
})

# camelCase -> snake_case rename for keys Google accepts under a different name.
_GEMINI_KEY_RENAMES = {
    "minLength": "min_length",
    "maxLength": "max_length",
    "minItems": "min_items",
    "maxItems": "max_items",
    "minProperties": "min_properties",
    "maxProperties": "max_properties",
    "anyOf": "any_of",
    "propertyOrdering": "property_ordering",
}


def _sanitize_schema_for_gemini(schema: Any) -> Any:
    """Recursively prune and normalize a JSON Schema for Google Gemini.

    Gemini's FunctionDeclaration.parameters accepts a strict subset of
    OpenAPI 3.0 schema. This sanitizer:
      - strips keys Gemini rejects (draft-7+ additions, $-prefixed meta, etc.)
      - renames camelCase keys Google accepts under snake_case names
      - coerces enum values to strings (Gemini requires list[str])
      - normalizes type:[A,null] unions to a single type + nullable:true
      - recurses into properties/items/any_of so nested violations are fixed
    """
    if isinstance(schema, list):
        return [_sanitize_schema_for_gemini(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in _GEMINI_STRIP_KEYS or key.startswith("$"):
            continue
        out_key = _GEMINI_KEY_RENAMES.get(key, key)
        cleaned[out_key] = value

    # Normalize type unions: Gemini has no multi-type; pick a concrete type
    # and promote 'null' (if present) into nullable:true.
    t = cleaned.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        if "null" in t:
            cleaned["nullable"] = True
        cleaned["type"] = non_null[0] if non_null else "string"

    # Gemini restricts `enum` to STRING type fields. If an enum is present,
    # force the type to string and stringify the values — otherwise the REST
    # layer rejects the whole request with
    # "only allowed for STRING type" even when the values themselves are fine.
    if isinstance(cleaned.get("enum"), list):
        cleaned["enum"] = [str(v) for v in cleaned["enum"]]
        cleaned["type"] = "string"

    # Recurse into sub-schemas.
    if isinstance(cleaned.get("properties"), dict):
        cleaned["properties"] = {
            k: _sanitize_schema_for_gemini(v)
            for k, v in cleaned["properties"].items()
        }
    if "items" in cleaned:
        cleaned["items"] = _sanitize_schema_for_gemini(cleaned["items"])
    if isinstance(cleaned.get("any_of"), list):
        cleaned["any_of"] = [_sanitize_schema_for_gemini(s) for s in cleaned["any_of"]]

    return cleaned


# Module-level client cache. google.genai.Client owns an aiohttp.ClientSession
# bound to the event loop it was first used on. When StreamingToolExecutor
# constructs a fresh provider per turn (or the wrapper path calls asyncio.run
# which creates a new loop each invocation), each client's finalizer eventually
# fires against a loop that has since been torn down, producing the noisy
# "got Future attached to a different loop" traceback at shutdown.
#
# Reusing a single Client per api_key across the process eliminates the leak.
# The Client is stateless w.r.t. requests so reuse is safe.
_CLIENT_CACHE: Dict[str, Any] = {}


def _get_or_create_client(genai_module, api_key: Optional[str]):
    """Return a process-wide cached genai.Client for this api_key."""
    cache_key = api_key or "__default__"
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = genai_module.Client(api_key=api_key) if api_key else genai_module.Client()
        _CLIENT_CACHE[cache_key] = client
    return client


class GoogleDirectProvider(LLMProvider):
    """Streams Gemini responses via the Google GenAI SDK.

    Implements the LLMProvider interface so the StreamingToolExecutor handles
    all tool orchestration — builtin tools (file_read, file_list, etc.) are
    dispatched through tool_execution.py rather than going to mcp_manager
    directly, fixing the "tool not found in any connected server" bug.
    """

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        api_key: Optional[str] = None,
        thinking_level: Optional[str] = None,
    ):
        self.model_id = model_id
        self.model_config = model_config
        self.thinking_level = thinking_level
        # Maps synthetic tool_use_id -> actual function name so
        # build_tool_result_message can reconstruct FunctionResponse.name.
        self._tool_id_to_name: Dict[str, str] = {}
        # Map tool_use_id -> thought_signature bytes (Gemini 3+ requirement).
        # Gemini returns an opaque per-turn signature on the Part that carries
        # a functionCall; it MUST be echoed back on the same Part in the next
        # request or the API rejects with 400 "missing a thought_signature".
        # Missing for older (2.x) models; we only echo back when present.
        self._tool_id_to_signature: Dict[str, bytes] = {}

        try:
            from google import genai
            from google.genai import types as gtypes
            self._genai = genai
            self._types = gtypes
        except ImportError:
            raise ImportError(
                "The 'google-genai' package is required for the Google endpoint. "
                "Install it with: pip install google-genai"
            )

        resolved_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.client = _get_or_create_client(self._genai, resolved_key)
        logger.info(f"GoogleDirectProvider: model={model_id}")

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
        try:
            contents, gen_config = self._build_request(messages, system_content, tools, config)
        except Exception as e:
            yield ErrorEvent(
                message=f"GoogleDirectProvider: request build failed: {e}",
                error_type=ErrorType.UNKNOWN,
            )
            return

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                async for event in self._do_stream(contents, gen_config):
                    yield event
                return
            except Exception as e:
                error_str = str(e)
                classified = self._classify_error(error_str)
                retryable = classified in (
                    ErrorType.THROTTLE, ErrorType.OVERLOADED, ErrorType.READ_TIMEOUT
                )
                if retryable and attempt < max_retries:
                    delay = 2 * (2 ** attempt)
                    logger.warning(
                        f"GoogleDirectProvider: {classified.name} retry "
                        f"{attempt + 1}/{max_retries} in {delay}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                yield ErrorEvent(message=error_str, error_type=classified, retryable=False)
                return

    def build_assistant_message(
        self,
        text: str,
        tool_uses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a Google-format model turn for conversation history."""
        parts = []
        if text.strip():
            parts.append({"text": text.rstrip()})
        for tu in tool_uses:
            fc_part: Dict[str, Any] = {
                "_function_call": {
                    "name": tu["name"],
                    "args": tu.get("input", {}),
                    "_id": tu["id"],
                }
            }
            # Echo back the thought_signature captured during streaming so
            # Gemini 3+ accepts the follow-up turn.
            sig = self._tool_id_to_signature.get(tu["id"])
            if sig:
                fc_part["_thought_signature"] = sig
            parts.append(fc_part)
        return {"role": "model", "parts": parts, "_google_native": True}

    def build_tool_result_message(
        self,
        tool_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a Google-format function-response turn for conversation history."""
        parts = []
        for tr in tool_results:
            tool_id = tr.get("tool_use_id", "")
            # Recover the original function name from the id->name map built
            # during streaming; fall back to the id itself if not found.
            fn_name = self._tool_id_to_name.get(tool_id, tool_id)
            content = tr["content"]
            if not isinstance(content, str):
                content = json.dumps(content)
            parts.append({
                "_function_response": {
                    "name": fn_name,
                    "response": {"content": content},
                }
            })
        return {"role": "user", "parts": parts, "_google_native": True}

    @property
    def provider_name(self) -> str:
        return "google"

    # ------------------------------------------------------------------
    # Internal: request building
    # ------------------------------------------------------------------

    def _build_request(
        self,
        messages: List[Dict[str, Any]],
        system_content: Optional[str],
        tools: List[Dict[str, Any]],
        config: ProviderConfig,
    ):
        types = self._types
        contents = self._convert_messages(messages)

        gen_config_params: Dict[str, Any] = {
            "temperature": config.temperature if config.temperature is not None else 0.3,
            "max_output_tokens": config.max_output_tokens,
            "safety_settings": [
                types.SafetySetting(
                    category=cat,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                )
                for cat in [
                    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                ]
            ],
        }

        if system_content:
            gen_config_params["system_instruction"] = system_content

        if self.thinking_level and (
            "gemini-3" in self.model_id.lower() or "thinking" in self.model_id.lower()
        ):
            gen_config_params["thinking_config"] = types.ThinkingConfig(
                thinking_level=self.thinking_level.upper()
            )

        if tools and not config.suppress_tools:
            declarations = self._convert_tools(tools)
            if declarations:
                google_tool = types.Tool(function_declarations=declarations)
                gen_config_params["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.AUTO
                    )
                )
                gen_config = types.GenerateContentConfig(**gen_config_params)
                gen_config.tools = [google_tool]
                return contents, gen_config

        return contents, types.GenerateContentConfig(**gen_config_params)

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-format + Google-native messages to Google contents list."""
        types = self._types
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            if msg.get("_google_native"):
                parts = []
                for p in msg.get("parts", []):
                    if "_function_call" in p:
                        fc = p["_function_call"]
                        part_kwargs: Dict[str, Any] = {
                            "function_call": types.FunctionCall(
                                name=fc["name"],
                                args=fc.get("args", {}),
                            )
                        }
                        # Attach thought_signature if we captured one during
                        # the originating turn (Gemini 3+ requirement).
                        if p.get("_thought_signature"):
                            part_kwargs["thought_signature"] = p["_thought_signature"]
                        parts.append(types.Part(**part_kwargs))
                    elif "_function_response" in p:
                        fr = p["_function_response"]
                        parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name=fr["name"],
                                response=fr.get("response", {}),
                            )
                        ))
                    elif "text" in p:
                        parts.append({"text": p["text"]})
                    else:
                        parts.append(p)
                google_role = "model" if role == "model" else "user"
                if parts:
                    contents.append({"role": google_role, "parts": parts})
            else:
                content = msg.get("content", "")
                google_role = "model" if role == "assistant" else "user"
                if isinstance(content, str):
                    if content.strip():
                        contents.append({"role": google_role, "parts": [{"text": content}]})
                elif isinstance(content, list):
                    parts = self._convert_content_blocks(content)
                    if parts:
                        contents.append({"role": google_role, "parts": parts})
        return contents

    def _convert_content_blocks(self, blocks: List[Any]) -> List[Any]:
        """Convert Anthropic/OpenAI multimodal content blocks to Google parts."""
        parts = []
        for block in blocks:
            if isinstance(block, str):
                parts.append({"text": block})
            elif isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append({"text": block.get("text", "")})
                elif btype == "image":
                    src = block.get("source", {})
                    if src.get("type") == "base64":
                        parts.append({"inline_data": {
                            "mime_type": src.get("media_type", "image/jpeg"),
                            "data": src.get("data", ""),
                        }})
                elif btype == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        header, data = url.split(",", 1)
                        mime = header.split(":")[1].split(";")[0]
                        parts.append({"inline_data": {"mime_type": mime, "data": data}})
                elif "text" in block:
                    parts.append({"text": block["text"]})
        return parts

    def _convert_tools(self, tools: List[Dict[str, Any]]):
        """Convert bedrock-format tool dicts to Google FunctionDeclaration list."""
        types = self._types
        declarations = []
        for tool in tools:
            name = tool.get("name", "")
            description = tool.get("description", "")
            schema = tool.get("input_schema", {})
            if not isinstance(schema, dict):
                schema = {}
            schema = _sanitize_schema_for_gemini(schema)
            try:
                declarations.append(types.FunctionDeclaration(
                    name=name,
                    description=description,
                    parameters=schema if schema else {"type": "object", "properties": {}},
                ))
            except Exception as e:
                logger.warning(f"GoogleDirectProvider: skipping tool '{name}': {e}")
        return declarations

    # ------------------------------------------------------------------
    # Internal: stream parsing
    # ------------------------------------------------------------------

    async def _do_stream(
        self,
        contents,
        gen_config,
    ) -> AsyncGenerator[StreamEvent, None]:
        response = await self.client.aio.models.generate_content_stream(
            model=self.model_id,
            contents=contents,
            config=gen_config,
        )

        tool_index = 0
        last_usage = None
        async for chunk in response:
            # Gemini attaches usage_metadata to chunks (typically the last).
            # Keep the most recent one and emit a single UsageEvent at the end
            # so streaming_tool_executor's cumulative tracker doesn't warn
            # "No usage metrics captured" every turn.
            um = getattr(chunk, "usage_metadata", None)
            if um is not None:
                last_usage = um
            if not chunk.parts:
                continue
            for part in chunk.parts:
                if part.text:
                    yield TextDelta(content=part.text)
                if hasattr(part, "thought") and part.thought:
                    yield ThinkingDelta(content=part.thought)
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_id = f"google_tool_{tool_index}"
                    tool_index += 1
                    args = dict(fc.args) if hasattr(fc, "args") and fc.args else {}
                    self._tool_id_to_name[tool_id] = fc.name
                    # Capture thought_signature if present (Gemini 3+).
                    sig = getattr(part, "thought_signature", None)
                    if sig:
                        self._tool_id_to_signature[tool_id] = sig
                    yield ToolUseStart(id=tool_id, name=fc.name, index=tool_index)
                    if args:
                        yield ToolUseInput(
                            partial_json=json.dumps(args),
                            index=tool_index,
                        )
                    yield ToolUseEnd(id=tool_id, name=fc.name, input=args, index=tool_index)

        if last_usage is not None:
            # Gemini's prompt_token_count already includes cached tokens, so
            # subtract cached to match other providers' "fresh input" semantics.
            def _as_int(v):
                try:
                    return int(v) if v is not None else 0
                except (TypeError, ValueError):
                    return 0
            prompt = _as_int(getattr(last_usage, "prompt_token_count", 0))
            cached = _as_int(getattr(last_usage, "cached_content_token_count", 0))
            output = _as_int(getattr(last_usage, "candidates_token_count", 0))
            yield UsageEvent(
                input_tokens=max(0, prompt - cached),
                output_tokens=output,
                cache_read_tokens=cached,
                cache_write_tokens=0,  # Gemini implicit caching has no write counter
            )

        yield StreamEnd(stop_reason="end_turn")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_error(error_str: str) -> ErrorType:
        lowered = error_str.lower()
        if "429" in error_str or "resource exhausted" in lowered or "quota" in lowered:
            return ErrorType.THROTTLE
        if "503" in error_str or "overloaded" in lowered or "unavailable" in lowered:
            return ErrorType.OVERLOADED
        if "timeout" in lowered:
            return ErrorType.READ_TIMEOUT
        if "context" in lowered and ("too long" in lowered or "too large" in lowered):
            return ErrorType.CONTEXT_LIMIT
        return ErrorType.UNKNOWN
