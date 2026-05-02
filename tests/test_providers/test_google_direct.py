"""
Tests for app.providers.google_direct — the Google Gemini provider.

Covers:
  1. Request building (system instruction, temperature, tools, thinking config)
  2. Message conversion (OpenAI-format → Google contents, Google-native passthrough)
  3. Content block conversion (text, image, image_url)
  4. Tool conversion (bedrock-format → FunctionDeclaration, schema cleaning)
  5. Stream parsing (text deltas, function calls, thinking deltas, empty chunks)
  6. Message formatting (build_assistant_message, build_tool_result_message)
  7. _tool_id_to_name mapping — the fix for "tool not found in any connected server"
  8. Retry logic (throttle retried, exhausted, non-retryable)
  9. Error classification
 10. Factory wiring
"""

from __future__ import annotations

import json
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Dict, List

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    ProviderConfig,
    StreamEnd,
    TextDelta,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseStart,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight fake Google SDK chunk objects
# ---------------------------------------------------------------------------

def _text_part(text: str) -> MagicMock:
    p = MagicMock()
    p.text = text
    p.function_call = None
    p.thought = None
    return p


def _fc_part(name: str, args: Dict[str, Any]) -> MagicMock:
    fc = MagicMock()
    fc.name = name
    fc.args = args
    p = MagicMock()
    p.text = None
    p.function_call = fc
    p.thought = None
    return p


def _thought_part(text: str) -> MagicMock:
    p = MagicMock()
    p.text = None
    p.function_call = None
    p.thought = text
    return p


def _chunk(parts: List[MagicMock]) -> MagicMock:
    c = MagicMock()
    c.parts = parts
    return c


def _empty_chunk() -> MagicMock:
    c = MagicMock()
    c.parts = None
    return c


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_google_client():
    client = MagicMock()
    client.aio = MagicMock()
    client.aio.models = MagicMock()
    return client


@pytest.fixture
def provider(mock_google_client):
    """GoogleDirectProvider with real google.genai.types but mocked HTTP client."""
    from google import genai
    with patch.object(genai, "Client", return_value=mock_google_client):
        from app.providers.google_direct import GoogleDirectProvider
        p = GoogleDirectProvider(
            model_id="gemini-2.5-pro",
            model_config={"family": "gemini"},
            api_key="test-api-key",
        )
    p.client = mock_google_client
    return p


@pytest.fixture
def thinking_provider(mock_google_client):
    """Provider with thinking_level set on a gemini-3 model."""
    from google import genai
    with patch.object(genai, "Client", return_value=mock_google_client):
        from app.providers.google_direct import GoogleDirectProvider
        p = GoogleDirectProvider(
            model_id="gemini-3-thinking",
            model_config={"family": "gemini"},
            api_key="test-api-key",
            thinking_level="high",
        )
    p.client = mock_google_client
    return p


@pytest.fixture
def basic_config():
    return ProviderConfig(max_output_tokens=4096, temperature=0.7)


def _wire_stream(provider, chunks):
    """Point generate_content_stream at a fake async iterator of chunks."""
    provider.client.aio.models.generate_content_stream = AsyncMock(
        return_value=_AsyncIter(chunks)
    )


async def _collect(provider, messages=None, system=None, tools=None, config=None):
    messages = messages or [{"role": "user", "content": "hi"}]
    config = config or ProviderConfig()
    events = []
    async for ev in provider.stream_response(messages, system, tools or [], config):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 1. Request building
# ---------------------------------------------------------------------------

class TestBuildRequest:
    def test_basic_contents_and_temperature(self, provider, basic_config):
        contents, gen_config = provider._build_request(
            [{"role": "user", "content": "hello"}], None, [], basic_config
        )
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert gen_config.temperature == 0.7
        assert gen_config.max_output_tokens == 4096

    def test_system_instruction_set(self, provider, basic_config):
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], "You are helpful.", [], basic_config
        )
        assert gen_config.system_instruction == "You are helpful."

    def test_no_system_when_none(self, provider, basic_config):
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], basic_config
        )
        assert not getattr(gen_config, "system_instruction", None)

    def test_temperature_defaults_to_03_when_none(self, provider):
        config = ProviderConfig(temperature=None, max_output_tokens=1024)
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], config
        )
        assert gen_config.temperature == 0.3

    def test_safety_settings_cover_four_categories(self, provider, basic_config):
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], basic_config
        )
        assert gen_config.safety_settings is not None
        assert len(gen_config.safety_settings) == 4

    def test_tools_attached_when_provided(self, provider, basic_config):
        tools = [{
            "name": "run_shell_command",
            "description": "Run a shell command",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }]
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], None, tools, basic_config
        )
        assert gen_config.tools is not None
        assert len(gen_config.tools) == 1

    def test_tools_omitted_when_suppressed(self, provider):
        tools = [{"name": "foo", "description": "bar", "input_schema": {}}]
        config = ProviderConfig(suppress_tools=True)
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], None, tools, config
        )
        assert not getattr(gen_config, "tools", None)

    def test_thinking_config_set_for_thinking_model(self, thinking_provider, basic_config):
        _, gen_config = thinking_provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], basic_config
        )
        assert getattr(gen_config, "thinking_config", None) is not None

    def test_thinking_config_absent_for_regular_model(self, provider, basic_config):
        _, gen_config = provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], basic_config
        )
        assert not getattr(gen_config, "thinking_config", None)


# ---------------------------------------------------------------------------
# 2. Message conversion — OpenAI-format input
# ---------------------------------------------------------------------------

class TestConvertMessages:
    def test_plain_user_string(self, provider):
        result = provider._convert_messages([{"role": "user", "content": "hello"}])
        assert result == [{"role": "user", "parts": [{"text": "hello"}]}]

    def test_plain_assistant_becomes_model_role(self, provider):
        result = provider._convert_messages([{"role": "assistant", "content": "hi"}])
        assert result[0]["role"] == "model"
        assert result[0]["parts"] == [{"text": "hi"}]

    def test_empty_string_message_omitted(self, provider):
        result = provider._convert_messages([{"role": "user", "content": "   "}])
        assert result == []

    def test_text_content_block(self, provider):
        result = provider._convert_messages([
            {"role": "user", "content": [{"type": "text", "text": "block text"}]}
        ])
        assert result[0]["parts"] == [{"text": "block text"}]

    def test_multiple_messages_preserved_in_order(self, provider):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ]
        result = provider._convert_messages(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "model"
        assert result[2]["role"] == "user"

    def test_google_native_function_call_passthrough(self, provider):
        """Google-native assistant message with function_call is preserved."""
        msg = {
            "role": "model",
            "_google_native": True,
            "parts": [
                {"_function_call": {"name": "file_read", "args": {"path": "x.py"}, "_id": "t0"}}
            ],
        }
        result = provider._convert_messages([msg])
        assert len(result) == 1
        assert result[0]["role"] == "model"
        part = result[0]["parts"][0]
        assert hasattr(part, "function_call")
        assert part.function_call.name == "file_read"

    def test_google_native_function_response_passthrough(self, provider):
        """Google-native user message with function_response is preserved."""
        msg = {
            "role": "user",
            "_google_native": True,
            "parts": [
                {"_function_response": {"name": "file_read", "response": {"content": "ok"}}}
            ],
        }
        result = provider._convert_messages([msg])
        assert len(result) == 1
        part = result[0]["parts"][0]
        assert hasattr(part, "function_response")
        assert part.function_response.name == "file_read"

    def test_google_native_text_part_passthrough(self, provider):
        msg = {
            "role": "model",
            "_google_native": True,
            "parts": [{"text": "some reply"}],
        }
        result = provider._convert_messages([msg])
        assert result[0]["parts"] == [{"text": "some reply"}]

    def test_google_native_empty_parts_omitted(self, provider):
        msg = {"role": "model", "_google_native": True, "parts": []}
        result = provider._convert_messages([msg])
        assert result == []

    def test_full_conversation_with_native_turns(self, provider):
        """Simulate a full tool-use conversation round-trip."""
        msgs = [
            {"role": "user", "content": "list files"},
            {
                "role": "model",
                "_google_native": True,
                "parts": [
                    {"_function_call": {"name": "run_shell_command", "args": {"command": "ls"}, "_id": "g0"}}
                ],
            },
            {
                "role": "user",
                "_google_native": True,
                "parts": [
                    {"_function_response": {"name": "run_shell_command", "response": {"content": "a.py\nb.py"}}}
                ],
            },
            {"role": "assistant", "content": "Found 2 files."},
        ]
        result = provider._convert_messages(msgs)
        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "model"
        assert result[2]["role"] == "user"
        assert result[3]["role"] == "model"


# ---------------------------------------------------------------------------
# 3. Content block conversion
# ---------------------------------------------------------------------------

class TestConvertContentBlocks:
    def test_text_block(self, provider):
        result = provider._convert_content_blocks([{"type": "text", "text": "hello"}])
        assert result == [{"text": "hello"}]

    def test_bare_string(self, provider):
        result = provider._convert_content_blocks(["plain string"])
        assert result == [{"text": "plain string"}]

    def test_base64_image(self, provider):
        result = provider._convert_content_blocks([{
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "abc123"},
        }])
        assert result == [{"inline_data": {"mime_type": "image/png", "data": "abc123"}}]

    def test_image_url_data_uri(self, provider):
        result = provider._convert_content_blocks([{
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,/9j/abc"},
        }])
        assert result == [{"inline_data": {"mime_type": "image/jpeg", "data": "/9j/abc"}}]

    def test_unknown_block_type_skipped(self, provider):
        result = provider._convert_content_blocks([{"type": "video", "url": "http://x"}])
        assert result == []

    def test_dict_with_text_key_fallback(self, provider):
        result = provider._convert_content_blocks([{"text": "fallback"}])
        assert result == [{"text": "fallback"}]


# ---------------------------------------------------------------------------
# 4. Tool conversion
# ---------------------------------------------------------------------------

class TestConvertTools:
    def test_basic_tool_declaration(self, provider):
        tools = [{
            "name": "file_read",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        assert declarations[0].name == "file_read"
        assert declarations[0].description == "Read a file"

    def test_schema_meta_keys_stripped(self, provider):
        """$schema, $defs, and title at top level must be removed — Google rejects them."""
        from google.genai.types import Schema
        tools = [{
            "name": "tool_x",
            "description": "desc",
            "input_schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$defs": {},
                "title": "ToolX",
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        params = declarations[0].parameters
        assert isinstance(params, Schema)
        assert str(params.type).upper() in ("OBJECT", "TYPE.OBJECT")
        prop_names = set(params.properties.keys()) if params.properties else set()
        assert "$schema" not in prop_names
        assert "$defs" not in prop_names
        assert "title" not in prop_names

    def test_empty_schema_gets_minimal_object(self, provider):
        from google.genai.types import Schema
        tools = [{"name": "noop", "description": "no-op", "input_schema": {}}]
        declarations = provider._convert_tools(tools)
        params = declarations[0].parameters
        assert isinstance(params, Schema)
        assert str(params.type).upper() in ("OBJECT", "TYPE.OBJECT")

    def test_non_dict_schema_gets_minimal_object(self, provider):
        from google.genai.types import Schema
        tools = [{"name": "bad", "description": "x", "input_schema": None}]
        declarations = provider._convert_tools(tools)
        params = declarations[0].parameters
        assert isinstance(params, Schema)
        assert str(params.type).upper() in ("OBJECT", "TYPE.OBJECT")

    def test_multiple_tools(self, provider):
        tools = [
            {"name": "a", "description": "tool a", "input_schema": {}},
            {"name": "b", "description": "tool b", "input_schema": {}},
        ]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 2
        assert declarations[0].name == "a"
        assert declarations[1].name == "b"

    def test_invalid_tool_skipped_without_raising(self, provider):
        """A tool that causes FunctionDeclaration to raise should be skipped gracefully."""
        from google.genai import types as gtypes
        with patch.object(gtypes, "FunctionDeclaration", side_effect=Exception("bad schema")):
            tools = [{"name": "broken", "description": "x", "input_schema": {}}]
            declarations = provider._convert_tools(tools)
        assert declarations == []

    # ---------------------------------------------------------------
    # Regression coverage for Gemini schema-sanitizer (the 400 / skip bug).
    # Each case mirrors a specific violation from the production log where
    # Gemini rejected our tool schemas:
    #   - exclusiveMinimum/Maximum         (mcp_fetch)
    #   - examples (plural)                 (mcp_Brazil* tools)
    #   - type: [A, B] union                (mcp_ApolloReadActions)
    #   - integer enum values               (mcp_QuipEditor)
    #   - additionalProperties (any depth)  (batch of ~80 tools)
    #   - camelCase keys Google accepts
    #     only in snake_case
    # ---------------------------------------------------------------

    def test_client_is_reused_across_provider_instances(self):
        """Multiple GoogleDirectProvider instances with the same api key must
        share a single genai.Client — otherwise each turn leaks a client +
        aiohttp session, producing 'attached to a different loop' tracebacks
        at shutdown."""
        import app.providers.google_direct as gd
        from app.providers.google_direct import GoogleDirectProvider
        gd._CLIENT_CACHE.clear()  # isolate from other tests
        p1 = GoogleDirectProvider(
            model_id="gemini-2.0-flash",
            model_config={"family": "gemini"},
            api_key="test-key",
        )
        p2 = GoogleDirectProvider(
            model_id="gemini-2.0-flash",
            model_config={"family": "gemini"},
            api_key="test-key",
        )
        assert p1.client is p2.client, "providers with same api_key must share client"
        p3 = GoogleDirectProvider(
            model_id="gemini-2.0-flash",
            model_config={"family": "gemini"},
            api_key="other-key",
        )
        assert p1.client is not p3.client, "different keys must get different clients"

    def test_exclusive_bounds_stripped(self, provider):
        """Gemini's Pydantic Schema rejects exclusiveMinimum/Maximum."""
        tools = [{
            "name": "fetch",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "max_length": {
                        "type": "integer",
                        "exclusiveMinimum": 0,
                        "exclusiveMaximum": 1000000,
                    },
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1, "tool with exclusive bounds must not be skipped"
        ml = declarations[0].parameters.properties["max_length"]
        # Pydantic Schema would have raised if these made it through.
        assert not hasattr(ml, "exclusiveMinimum") or ml.exclusiveMinimum is None
        assert not hasattr(ml, "exclusiveMaximum") or ml.exclusiveMaximum is None

    def test_examples_plural_stripped(self, provider):
        """Google accepts singular 'example' only; 'examples' must be dropped."""
        tools = [{
            "name": "brazil_build",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workingDirectory": {
                        "type": "string",
                        "examples": ["/path/to/workspace", "MyPackage"],
                    },
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1

    def test_type_union_collapsed_with_nullable(self, provider):
        """type: [string, null] -> type=string, nullable=true."""
        tools = [{
            "name": "apollo",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "marker": {"type": ["string", "null"]},
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        marker = declarations[0].parameters.properties["marker"]
        assert str(marker.type).upper() in ("STRING", "TYPE.STRING")
        assert marker.nullable is True

    def test_type_union_without_null_picks_first(self, provider):
        """type: [string, number] -> type=string (no union support in Gemini)."""
        tools = [{
            "name": "apollo",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "marker": {"type": ["string", "number"]},
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        marker = declarations[0].parameters.properties["marker"]
        assert str(marker.type).upper() in ("STRING", "TYPE.STRING")

    def test_integer_enum_coerced_to_strings(self, provider):
        """Gemini requires enum: list[str]; integer enums must be stringified."""
        tools = [{
            "name": "quip_editor",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "integer", "enum": [0, 1, 2, 3]},
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        loc = declarations[0].parameters.properties["location"]
        assert loc.enum == ["0", "1", "2", "3"]

    def test_integer_enum_coerces_type_to_string(self, provider):
        """Gemini rejects enum on non-string types ('only allowed for STRING
        type'). When enum is present the field's type must be coerced to
        string alongside stringifying the values."""
        from app.providers.google_direct import _sanitize_schema_for_gemini
        schema = {
            "type": "number",
            "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        }
        out = _sanitize_schema_for_gemini(schema)
        assert out["type"] == "string"
        assert out["enum"] == ["0", "1", "2", "3", "4", "5", "6", "7",
                               "8", "9", "10", "11"]

    def test_additional_properties_stripped_at_top_level(self, provider):
        """REST rejects additionalProperties even though Pydantic accepts it."""
        tools = [{
            "name": "tool_x",
            "description": "x",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"a": {"type": "string"}},
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        # Pydantic Schema would accept but REST rejects; ensure the serialized
        # schema does not include additional_properties.
        params = declarations[0].parameters
        dumped = params.model_dump(exclude_none=True)
        assert "additional_properties" not in dumped
        assert "additionalProperties" not in dumped

    def test_additional_properties_stripped_nested(self, provider):
        """additionalProperties must be stripped inside items/properties too."""
        tools = [{
            "name": "tool_nested",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"col": {"type": "string"}},
                        },
                    },
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        items = declarations[0].parameters.properties["rows"].items
        dumped = items.model_dump(exclude_none=True)
        assert "additional_properties" not in dumped
        assert "additionalProperties" not in dumped

    def test_camel_case_keys_renamed_to_snake(self, provider):
        """minLength/maxLength -> min_length/max_length (Google's field names)."""
        tools = [{
            "name": "tool_camel",
            "description": "x",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                },
            },
        }]
        declarations = provider._convert_tools(tools)
        assert len(declarations) == 1
        name = declarations[0].parameters.properties["name"]
        assert name.min_length == 1
        assert name.max_length == 80


# ---------------------------------------------------------------------------
# 5. Stream parsing
# ---------------------------------------------------------------------------

class TestStreamParsing:
    @pytest.mark.asyncio
    async def test_text_streaming(self, provider):
        _wire_stream(provider, [
            _chunk([_text_part("Hello")]),
            _chunk([_text_part(" world")]),
        ])
        events = await _collect(provider)
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert len(text_events) == 2
        assert text_events[0].content == "Hello"
        assert text_events[1].content == " world"

    @pytest.mark.asyncio
    async def test_stream_ends_with_stream_end(self, provider):
        _wire_stream(provider, [_chunk([_text_part("hi")])])
        events = await _collect(provider)
        assert isinstance(events[-1], StreamEnd)
        assert events[-1].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_empty_stream_still_yields_stream_end(self, provider):
        _wire_stream(provider, [])
        events = await _collect(provider)
        assert len(events) == 1
        assert isinstance(events[0], StreamEnd)

    @pytest.mark.asyncio
    async def test_empty_chunk_parts_skipped(self, provider):
        _wire_stream(provider, [_empty_chunk(), _chunk([_text_part("ok")])])
        events = await _collect(provider)
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert len(text_events) == 1
        assert text_events[0].content == "ok"

    @pytest.mark.asyncio
    async def test_function_call_yields_tool_use_start_and_end(self, provider):
        _wire_stream(provider, [
            _chunk([_fc_part("file_read", {"path": "app/main.py"})]),
        ])
        events = await _collect(provider)
        starts = [e for e in events if isinstance(e, ToolUseStart)]
        ends = [e for e in events if isinstance(e, ToolUseEnd)]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0].name == "file_read"
        assert ends[0].name == "file_read"
        assert ends[0].input == {"path": "app/main.py"}

    @pytest.mark.asyncio
    async def test_function_call_tool_id_is_synthetic(self, provider):
        _wire_stream(provider, [_chunk([_fc_part("my_tool", {})])])
        events = await _collect(provider)
        start = next(e for e in events if isinstance(e, ToolUseStart))
        assert start.id.startswith("google_tool_")

    @pytest.mark.asyncio
    async def test_multiple_function_calls_get_distinct_ids(self, provider):
        _wire_stream(provider, [
            _chunk([_fc_part("tool_a", {"x": 1})]),
            _chunk([_fc_part("tool_b", {"y": 2})]),
        ])
        events = await _collect(provider)
        starts = [e for e in events if isinstance(e, ToolUseStart)]
        assert len(starts) == 2
        assert starts[0].id != starts[1].id
        assert starts[0].name == "tool_a"
        assert starts[1].name == "tool_b"

    @pytest.mark.asyncio
    async def test_thinking_delta_emitted(self, provider):
        _wire_stream(provider, [_chunk([_thought_part("reasoning here")])])
        events = await _collect(provider)
        thinking = [e for e in events if isinstance(e, ThinkingDelta)]
        assert len(thinking) == 1
        assert thinking[0].content == "reasoning here"

    @pytest.mark.asyncio
    async def test_mixed_text_and_tool_in_one_chunk(self, provider):
        _wire_stream(provider, [
            _chunk([_text_part("Let me check."), _fc_part("file_list", {"path": "."})]),
        ])
        events = await _collect(provider)
        assert any(isinstance(e, TextDelta) for e in events)
        assert any(isinstance(e, ToolUseStart) for e in events)

    @pytest.mark.asyncio
    async def test_function_call_with_none_args(self, provider):
        part = _fc_part("no_args_tool", {})
        part.function_call.args = None  # some SDK versions return None
        _wire_stream(provider, [_chunk([part])])
        events = await _collect(provider)
        end = next(e for e in events if isinstance(e, ToolUseEnd))
        assert end.input == {}

    @pytest.mark.asyncio
    async def test_tool_use_input_emitted_with_args(self, provider):
        """ToolUseInput carries the full JSON so the executor accumulates partial_json."""
        from app.providers.base import ToolUseInput
        _wire_stream(provider, [_chunk([_fc_part("run_shell_command", {"command": "ls -la"})])])
        events = await _collect(provider)
        inputs = [e for e in events if isinstance(e, ToolUseInput)]
        assert len(inputs) == 1
        assert json.loads(inputs[0].partial_json) == {"command": "ls -la"}

    @pytest.mark.asyncio
    async def test_tool_use_input_ordering(self, provider):
        """ToolUseInput must appear between ToolUseStart and ToolUseEnd."""
        from app.providers.base import ToolUseInput
        _wire_stream(provider, [_chunk([_fc_part("file_read", {"path": "foo.py"})])])
        events = await _collect(provider)
        types_seq = [type(e).__name__ for e in events
                     if type(e).__name__ in ("ToolUseStart", "ToolUseInput", "ToolUseEnd")]
        assert types_seq == ["ToolUseStart", "ToolUseInput", "ToolUseEnd"]

    @pytest.mark.asyncio
    async def test_no_tool_use_input_when_args_empty(self, provider):
        """No ToolUseInput should be emitted when args are empty/None."""
        from app.providers.base import ToolUseInput
        part = _fc_part("no_args_tool", {})
        part.function_call.args = None
        _wire_stream(provider, [_chunk([part])])
        events = await _collect(provider)
        assert not any(isinstance(e, ToolUseInput) for e in events)

    @pytest.mark.asyncio
    async def test_tool_use_input_index_matches_start(self, provider):
        """ToolUseInput.index must match ToolUseStart.index for the executor to correlate."""
        from app.providers.base import ToolUseInput
        _wire_stream(provider, [_chunk([_fc_part("grep", {"pattern": "foo"})])])
        events = await _collect(provider)
        start = next(e for e in events if isinstance(e, ToolUseStart))
        inp = next(e for e in events if isinstance(e, ToolUseInput))
        assert inp.index == start.index

    @pytest.mark.asyncio
    async def test_multiple_tools_each_get_input(self, provider):
        """Each tool call gets its own ToolUseInput with the correct JSON."""
        from app.providers.base import ToolUseInput
        _wire_stream(provider, [_chunk([
            _fc_part("tool_a", {"x": 1}),
            _fc_part("tool_b", {"y": 2}),
        ])])
        events = await _collect(provider)
        inputs = [e for e in events if isinstance(e, ToolUseInput)]
        assert len(inputs) == 2
        parsed = [json.loads(e.partial_json) for e in inputs]
        assert {"x": 1} in parsed
        assert {"y": 2} in parsed


# ---------------------------------------------------------------------------
# 6. Message formatting
# ---------------------------------------------------------------------------

class TestBuildAssistantMessage:
    def test_text_only(self, provider):
        msg = provider.build_assistant_message("Hello!", [])
        assert msg["role"] == "model"
        assert msg["_google_native"] is True
        assert msg["parts"] == [{"text": "Hello!"}]

    def test_whitespace_only_text_excluded(self, provider):
        msg = provider.build_assistant_message("   ", [])
        assert msg["parts"] == []

    def test_text_trailing_whitespace_stripped(self, provider):
        msg = provider.build_assistant_message("Hello   ", [])
        assert msg["parts"][0]["text"] == "Hello"

    def test_tool_use_appended(self, provider):
        tool_uses = [{"id": "t0", "name": "file_read", "input": {"path": "x.py"}}]
        msg = provider.build_assistant_message("Sure.", tool_uses)
        assert len(msg["parts"]) == 2
        fc = msg["parts"][1]["_function_call"]
        assert fc["name"] == "file_read"
        assert fc["args"] == {"path": "x.py"}
        assert fc["_id"] == "t0"

    def test_multiple_tool_uses(self, provider):
        tool_uses = [
            {"id": "t0", "name": "file_read", "input": {"path": "a.py"}},
            {"id": "t1", "name": "file_list", "input": {"path": "."}},
        ]
        msg = provider.build_assistant_message("", tool_uses)
        fcs = [p["_function_call"] for p in msg["parts"] if "_function_call" in p]
        assert len(fcs) == 2
        assert fcs[0]["name"] == "file_read"
        assert fcs[1]["name"] == "file_list"


class TestBuildToolResultMessage:
    def test_single_result(self, provider):
        provider._tool_id_to_name["google_tool_0"] = "file_read"
        msg = provider.build_tool_result_message([
            {"tool_use_id": "google_tool_0", "content": "file contents here"}
        ])
        assert msg["role"] == "user"
        assert msg["_google_native"] is True
        fr = msg["parts"][0]["_function_response"]
        assert fr["name"] == "file_read"
        assert fr["response"] == {"content": "file contents here"}

    def test_unknown_id_falls_back_to_id_as_name(self, provider):
        msg = provider.build_tool_result_message([
            {"tool_use_id": "orphan_id", "content": "result"}
        ])
        fr = msg["parts"][0]["_function_response"]
        assert fr["name"] == "orphan_id"

    def test_non_string_content_json_serialized(self, provider):
        provider._tool_id_to_name["google_tool_0"] = "list_tool"
        msg = provider.build_tool_result_message([
            {"tool_use_id": "google_tool_0", "content": {"files": ["a.py", "b.py"]}}
        ])
        fr = msg["parts"][0]["_function_response"]
        assert fr["response"]["content"] == '{"files": ["a.py", "b.py"]}'

    def test_multiple_results(self, provider):
        provider._tool_id_to_name["google_tool_0"] = "tool_a"
        provider._tool_id_to_name["google_tool_1"] = "tool_b"
        msg = provider.build_tool_result_message([
            {"tool_use_id": "google_tool_0", "content": "result_a"},
            {"tool_use_id": "google_tool_1", "content": "result_b"},
        ])
        assert len(msg["parts"]) == 2
        assert msg["parts"][0]["_function_response"]["name"] == "tool_a"
        assert msg["parts"][1]["_function_response"]["name"] == "tool_b"


# ---------------------------------------------------------------------------
# 7. _tool_id_to_name mapping (the core bug fix)
# ---------------------------------------------------------------------------

class TestToolIdToNameMapping:
    @pytest.mark.asyncio
    async def test_mapping_populated_during_stream(self, provider):
        _wire_stream(provider, [
            _chunk([_fc_part("file_read", {"path": "x.py"})]),
        ])
        await _collect(provider)
        assert "google_tool_0" in provider._tool_id_to_name
        assert provider._tool_id_to_name["google_tool_0"] == "file_read"

    @pytest.mark.asyncio
    async def test_mapping_used_by_build_tool_result_message(self, provider):
        """Full round-trip: stream a tool call, build the result message,
        verify FunctionResponse carries the actual function name not the synthetic id."""
        _wire_stream(provider, [
            _chunk([_fc_part("run_shell_command", {"command": "ls"})]),
        ])
        events = await _collect(provider)

        start = next(e for e in events if isinstance(e, ToolUseStart))
        msg = provider.build_tool_result_message([
            {"tool_use_id": start.id, "content": "file1.py\nfile2.py"}
        ])
        fr = msg["parts"][0]["_function_response"]
        # Without the fix this would be "google_tool_0" (the synthetic id).
        assert fr["name"] == "run_shell_command"

    @pytest.mark.asyncio
    async def test_multiple_calls_all_mapped(self, provider):
        _wire_stream(provider, [
            _chunk([_fc_part("file_read", {"path": "a.py"})]),
            _chunk([_fc_part("file_list", {"path": "."})]),
        ])
        await _collect(provider)
        assert provider._tool_id_to_name["google_tool_0"] == "file_read"
        assert provider._tool_id_to_name["google_tool_1"] == "file_list"


# ---------------------------------------------------------------------------
# 8. Retry logic
# ---------------------------------------------------------------------------

class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_throttle_error_is_retried(self, provider):
        call_count = 0

        async def mock_stream(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("429 resource exhausted")
            return _AsyncIter([_chunk([_text_part("ok")])])

        provider.client.aio.models.generate_content_stream = mock_stream

        with patch("app.providers.google_direct.asyncio.sleep", new_callable=AsyncMock):
            events = await _collect(provider)

        assert call_count == 3
        assert any(isinstance(e, TextDelta) for e in events)

    @pytest.mark.asyncio
    async def test_throttle_exhausted_yields_error_event(self, provider):
        async def always_throttle(**kwargs):
            raise Exception("429 quota exceeded")

        provider.client.aio.models.generate_content_stream = always_throttle

        with patch("app.providers.google_direct.asyncio.sleep", new_callable=AsyncMock):
            events = await _collect(provider)

        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].error_type == ErrorType.THROTTLE

    @pytest.mark.asyncio
    async def test_non_retryable_error_not_retried(self, provider):
        call_count = 0

        async def auth_error(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("403 permission denied")

        provider.client.aio.models.generate_content_stream = auth_error

        with patch("app.providers.google_direct.asyncio.sleep", new_callable=AsyncMock):
            events = await _collect(provider)

        assert call_count == 1
        assert isinstance(events[0], ErrorEvent)

    @pytest.mark.asyncio
    async def test_overload_error_is_retried(self, provider):
        call_count = 0

        async def overloaded(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("503 service overloaded")
            return _AsyncIter([])

        provider.client.aio.models.generate_content_stream = overloaded

        with patch("app.providers.google_direct.asyncio.sleep", new_callable=AsyncMock):
            events = await _collect(provider)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_request_build_failure_yields_error(self, provider):
        with patch.object(provider, "_build_request", side_effect=ValueError("bad config")):
            events = await _collect(provider)

        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert "request build failed" in events[0].message


# ---------------------------------------------------------------------------
# 9. Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def _cls(self, msg):
        from app.providers.google_direct import GoogleDirectProvider
        return GoogleDirectProvider._classify_error(msg)

    def test_429(self):
        assert self._cls("Error 429") == ErrorType.THROTTLE

    def test_resource_exhausted(self):
        assert self._cls("RESOURCE_EXHAUSTED: quota exceeded") == ErrorType.THROTTLE

    def test_quota(self):
        assert self._cls("quota limit reached") == ErrorType.THROTTLE

    def test_503(self):
        assert self._cls("503 service unavailable") == ErrorType.OVERLOADED

    def test_overloaded(self):
        assert self._cls("model overloaded") == ErrorType.OVERLOADED

    def test_unavailable(self):
        assert self._cls("service unavailable") == ErrorType.OVERLOADED

    def test_timeout(self):
        assert self._cls("request timeout after 60s") == ErrorType.READ_TIMEOUT

    def test_context_too_long(self):
        assert self._cls("context too long for this model") == ErrorType.CONTEXT_LIMIT

    def test_context_too_large(self):
        assert self._cls("input context too large") == ErrorType.CONTEXT_LIMIT

    def test_unknown_error(self):
        assert self._cls("Something completely unexpected") == ErrorType.UNKNOWN


# ---------------------------------------------------------------------------
# 10. Factory wiring
# ---------------------------------------------------------------------------

class TestFactoryWiring:
    def test_google_endpoint_returns_google_provider(self):
        from google import genai
        mock_client = MagicMock()
        with patch.object(genai, "Client", return_value=mock_client):
            from app.providers.factory import create_provider
            from app.providers.google_direct import GoogleDirectProvider
            provider = create_provider(
                endpoint="google",
                model_id="gemini-2.5-pro",
                model_config={"family": "gemini"},
                api_key="test-key",
            )
        assert isinstance(provider, GoogleDirectProvider)
        assert provider.provider_name == "google"
        assert provider.model_id == "gemini-2.5-pro"

    def test_google_api_key_from_env(self):
        import os
        from google import genai
        mock_client = MagicMock()
        with patch.object(genai, "Client", return_value=mock_client):
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "sk-env-key"}):
                from app.providers.factory import create_provider
                from app.providers.google_direct import GoogleDirectProvider
                provider = create_provider(
                    endpoint="google",
                    model_id="gemini-2.5-pro",
                    model_config={},
                )
        assert isinstance(provider, GoogleDirectProvider)

    def test_thinking_level_passed_through(self):
        from google import genai
        mock_client = MagicMock()
        with patch.object(genai, "Client", return_value=mock_client):
            from app.providers.factory import create_provider
            provider = create_provider(
                endpoint="google",
                model_id="gemini-3-thinking",
                model_config={"family": "gemini", "thinking_level": "high"},
                api_key="test-key",
            )
        assert provider.thinking_level == "high"

    def test_google_not_returned_for_other_endpoints(self):
        from app.providers.factory import create_provider
        from app.providers.google_direct import GoogleDirectProvider
        with patch.dict(sys.modules, {"openai": MagicMock()}):
            provider = create_provider(
                endpoint="openai",
                model_id="gpt-4.1",
                model_config={},
                api_key="sk-test",
            )
        assert not isinstance(provider, GoogleDirectProvider)

    def test_unsupported_endpoint_error_message_lists_google(self):
        from app.providers.factory import create_provider
        with pytest.raises(ValueError, match="google"):
            create_provider(endpoint="unknown_xyz", model_id="x")
