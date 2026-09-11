"""Tool schema compaction: lossless shrinking of the per-request tool block.

Two layers:

* ``compact_tool_schema`` itself — what it strips, what it must never touch.
* The seam — ``DirectMCPTool`` / ``SecureMCPTool`` must *carry* the compacted
  schema in ``metadata["input_schema"]``, and each provider converter must
  read it from there.  A correct compactor that nothing calls saves zero
  tokens, so the seam is asserted at the outermost surface (the provider
  payload), not on the helper.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest
from pydantic import BaseModel, Field

from app.utils.tool_schema_compact import compact_tool_schema


# ---------------------------------------------------------------------------
# Fixtures: a Pydantic model shaped like the builtins, and a hostile schema
# ---------------------------------------------------------------------------

class SampleInput(BaseModel):
    """Input schema for sample_tool."""
    path: str = Field(description="Where to read.")
    title: Optional[str] = Field(default=None, description="A param literally named title.")
    limit: int = Field(default=10, description="Non-null default must survive.")
    mode: Optional[str] = Field(default=None, description="Optional with null arm.")
    tag: Optional[str] = Field(description="Required but nullable.")


def _raw() -> dict:
    return SampleInput.model_json_schema()


# ---------------------------------------------------------------------------
# Compactor unit behaviour
# ---------------------------------------------------------------------------

class TestCompactorStrips:
    def test_is_smaller_and_strips_title_keywords(self):
        raw = _raw()
        out = compact_tool_schema(raw, strip_root_description=True)
        assert len(json.dumps(out)) < len(json.dumps(raw))
        assert "title" not in out
        for prop in out["properties"].values():
            assert "title" not in prop

    def test_root_description_stripped_only_when_asked(self):
        raw = _raw()
        assert raw["description"] == "Input schema for sample_tool."
        assert "description" not in compact_tool_schema(raw, strip_root_description=True)
        assert compact_tool_schema(raw)["description"] == "Input schema for sample_tool."

    def test_optional_anyof_null_collapsed(self):
        out = compact_tool_schema(_raw())
        mode = out["properties"]["mode"]
        assert "anyOf" not in mode
        assert mode["type"] == "string"
        assert mode["description"] == "Optional with null arm."
        assert "default" not in mode  # default: null removed

    def test_non_null_default_kept(self):
        out = compact_tool_schema(_raw())
        assert out["properties"]["limit"]["default"] == 10

    def test_schema_uri_removed(self):
        out = compact_tool_schema({"$schema": "http://json-schema.org/draft-07/schema#",
                                   "type": "object", "properties": {}})
        assert "$schema" not in out

    def test_non_dict_passthrough_and_no_mutation(self):
        assert compact_tool_schema(None) is None
        assert compact_tool_schema("x") == "x"
        raw = _raw()
        snapshot = json.dumps(raw, sort_keys=True)
        compact_tool_schema(raw, strip_root_description=True)
        assert json.dumps(raw, sort_keys=True) == snapshot


class TestCompactorPreserves:
    """Anything the validator or the model relies on must be untouched."""

    def test_property_names_and_required_unchanged(self):
        raw = _raw()
        out = compact_tool_schema(raw, strip_root_description=True)
        assert set(out["properties"]) == set(raw["properties"])
        assert out["required"] == raw["required"]

    def test_property_named_title_is_kept(self):
        # 'title' as a *property name* is data, not the schema keyword.
        out = compact_tool_schema(_raw())
        assert "title" in out["properties"]
        assert out["properties"]["title"]["description"] == "A param literally named title."

    def test_required_nullable_keeps_union(self):
        raw = _raw()
        assert "tag" in raw["required"]
        out = compact_tool_schema(raw)
        assert "anyOf" in out["properties"]["tag"]
        assert {"type": "null"} in out["properties"]["tag"]["anyOf"]

    def test_enum_untouched_including_nested_refs(self):
        raw = {
            "type": "object",
            "properties": {
                "fmt": {"$ref": "#/$defs/Fmt", "default": "png"},
                "items": {"type": "array", "items": {"title": "Item", "type": "string"}},
            },
            "$defs": {"Fmt": {"title": "Fmt", "enum": ["png", "svg"], "type": "string"}},
        }
        out = compact_tool_schema(raw)
        assert out["$defs"]["Fmt"]["enum"] == ["png", "svg"]
        assert "title" not in out["$defs"]["Fmt"]
        assert out["properties"]["fmt"]["$ref"] == "#/$defs/Fmt"
        assert out["properties"]["items"]["items"] == {"type": "string"}

    def test_real_union_not_collapsed(self):
        raw = {"type": "object", "properties": {
            "v": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}}}
        out = compact_tool_schema(raw)
        assert len(out["properties"]["v"]["anyOf"]) == 3

    def test_null_arm_with_meaning_not_dropped(self):
        raw = {"type": "object", "properties": {
            "v": {"anyOf": [{"type": "string"}, {"type": "null", "description": "clear"}]}}}
        out = compact_tool_schema(raw)
        assert "anyOf" in out["properties"]["v"]

    def test_additional_properties_kept(self):
        raw = {"type": "object", "additionalProperties": False, "properties": {}}
        assert compact_tool_schema(raw)["additionalProperties"] is False


class TestValidatorCompatibility:
    """The compacted schema is also what the executor validates against."""

    def test_expected_type_matches_before_and_after(self):
        from app.streaming_tool_executor import _schema_expected_type
        raw = _raw()
        out = compact_tool_schema(raw)
        for name in raw["properties"]:
            assert _schema_expected_type(raw["properties"][name]) == \
                _schema_expected_type(out["properties"][name]), name

    def test_validation_verdict_identical(self):
        from app.streaming_tool_executor import validate_tool_args_against_schema
        raw = _raw()
        out = compact_tool_schema(raw, strip_root_description=True)
        for args in ({"path": "a", "tag": None}, {"path": "a", "tag": "t"},
                     {"tag": "t"}, {"path": "a", "tag": "t", "mode": None}):
            assert (validate_tool_args_against_schema("t", args, raw) is None) == \
                (validate_tool_args_against_schema("t", args, out) is None), args


# ---------------------------------------------------------------------------
# Seam: wrapped tools carry the compacted schema and providers read it
# ---------------------------------------------------------------------------

class _FakeBuiltin:
    name = "sample_tool"
    description = "A sample."
    is_internal = False
    InputSchema = SampleInput

    async def execute(self, **kwargs):
        return "ok"


@pytest.fixture
def direct_tool():
    from app.mcp.enhanced_tools import DirectMCPTool
    return DirectMCPTool(_FakeBuiltin())


class TestDirectToolSeam:
    def test_metadata_carries_compacted_schema(self, direct_tool):
        schema = direct_tool.metadata.get("input_schema")
        assert isinstance(schema, dict), "DirectMCPTool must publish a schema in metadata"
        assert "title" not in schema
        assert "description" not in schema  # Pydantic docstring stripped
        assert "anyOf" not in schema["properties"]["mode"]
        assert schema["required"] == ["path", "tag"]

    def test_args_schema_still_pydantic_for_langchain_validation(self, direct_tool):
        # Compaction must not replace the Pydantic class LangChain validates with.
        assert direct_tool.args_schema is SampleInput


class TestSecureToolSeam:
    def test_external_schema_compacted_but_root_description_kept(self):
        from app.mcp.enhanced_tools import SecureMCPTool
        raw = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "description": "Server-provided guidance.",
            "properties": {"q": {"title": "Q", "type": "string"},
                           "n": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}},
            "required": ["q"],
        }
        t = SecureMCPTool(name="mcp_x", description="d", mcp_tool_name="x", input_schema=raw)
        s = t.metadata["input_schema"]
        assert "$schema" not in s
        assert s["description"] == "Server-provided guidance."
        assert s["properties"]["q"] == {"type": "string"}
        assert s["properties"]["n"] == {"type": "integer"}


class TestProviderConvertersReadMetadata:
    """Every converter must emit the compacted schema, not re-derive the raw one."""

    def _has_title(self, obj) -> bool:
        """True if any schema NODE carries a ``title`` key.

        Walks the tree rather than string-searching the JSON: the fixture
        has a parameter literally named ``title``, which is a key of a
        ``properties`` map, not a schema annotation, and must not count.
        """
        if isinstance(obj, list):
            return any(self._has_title(x) for x in obj)
        if not isinstance(obj, dict):
            return False
        # ``obj`` is a schema node here (properties maps are never passed
        # in — we descend straight into their values below).
        if "title" in obj:
            return True
        for k, v in obj.items():
            if k == "properties" and isinstance(v, dict):
                if any(self._has_title(p) for p in v.values()):
                    return True
            elif k != "title" and self._has_title(v):
                return True
        return False

    def test_openai_direct(self, direct_tool):
        from app.agents.wrappers.openai_direct import DirectOpenAIModel
        conv = DirectOpenAIModel._convert_langchain_tools_to_openai
        out = conv(object.__new__(DirectOpenAIModel), [direct_tool])
        params = out[0]["function"]["parameters"]
        assert params is direct_tool.metadata["input_schema"] or params == direct_tool.metadata["input_schema"]
        assert not self._has_title(params)

    def test_anthropic_direct(self, direct_tool):
        from app.agents.wrappers.anthropic_direct import DirectAnthropicModel
        out = DirectAnthropicModel._convert_tools(object.__new__(DirectAnthropicModel), [direct_tool])
        assert out[0]["input_schema"] == direct_tool.metadata["input_schema"]

    def test_bedrock_executor(self, direct_tool):
        from app.streaming_tool_executor import StreamingToolExecutor
        out = StreamingToolExecutor._convert_tool_schema(object.__new__(StreamingToolExecutor), direct_tool)
        assert out["input_schema"] == direct_tool.metadata["input_schema"]

    def test_google_direct(self, direct_tool):
        google = pytest.importorskip("app.agents.wrappers.google_direct")
        pytest.importorskip("google.genai")
        model = object.__new__(google.DirectGoogleModel)
        tool = model._convert_langchain_tools_to_google([direct_tool])
        decl = tool.function_declarations[0]
        params = decl.parameters
        dumped = params if isinstance(params, dict) else params.model_dump(exclude_none=True)
        # Gemini sanitizer may further prune, but must start from the compacted
        # schema: the Pydantic docstring must not leak through as a description.
        assert dumped.get("description") is None
