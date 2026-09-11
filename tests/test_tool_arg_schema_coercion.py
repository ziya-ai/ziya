"""
Schema-driven coercion of tool arguments before validation.

Weak models (observed: qwen2.5-coder:7b on Ollama) quote scalars —
``"nextThoughtNeeded": "false"``, ``"thoughtNumber": "1"`` — or JSON-encode
an object argument. External MCP tools were repaired at dispatch by
``MCPManager._coerce_argument_types``; builtin (dynamic-loader) tools were
not, and the executor's own validation ran on the raw values.

``coerce_tool_args_to_schema`` runs once for every tool, before
``validate_tool_args_against_schema``. These tests cover the pure function
and the seam in the executor's stream loop.
"""

import inspect
import json
import re

import pytest

from app.streaming_tool_executor import (
    StreamingToolExecutor,
    coerce_tool_args_to_schema,
    validate_tool_args_against_schema,
)

SEQ_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "nextThoughtNeeded": {"type": ["boolean", "string"]},
        "thoughtNumber": {"type": "integer"},
        "totalThoughts": {"type": "integer"},
        "isRevision": {"type": ["boolean", "string"]},
        "revisesThought": {"type": "integer"},
    },
    "required": ["thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts"],
}

STRICT_SCHEMA = {
    "type": "object",
    "properties": {
        "flag": {"type": "boolean"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "opts": {"type": "object"},
        "items": {"type": "array"},
        "name": {"type": "string"},
        "level": {"type": "integer", "enum": [1, 2, 3]},
        "maybe": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "either": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
        "nullable_bool": {"type": ["boolean", "null"]},
    },
}


# ---------------------------------------------------------------------------
# The live case
# ---------------------------------------------------------------------------

class TestLiveQwenCall:
    def test_quoted_boolean_and_numbers_are_typed(self):
        # What qwen2.5-coder:7b actually emitted (arguments object).
        args = {
            "thought": "Yes, I can hear you. How can I assist you today?",
            "nextThoughtNeeded": "false",
            "thoughtNumber": "1",
            "totalThoughts": "1",
        }
        out = coerce_tool_args_to_schema(args, STRICT_SCHEMA | {
            "properties": {
                "thought": {"type": "string"},
                "nextThoughtNeeded": {"type": "boolean"},
                "thoughtNumber": {"type": "integer"},
                "totalThoughts": {"type": "integer"},
            }
        })
        assert out["nextThoughtNeeded"] is False
        assert out["thoughtNumber"] == 1 and isinstance(out["thoughtNumber"], int)
        assert out["totalThoughts"] == 1 and isinstance(out["totalThoughts"], int)
        assert out["thought"] == args["thought"]

    def test_union_bool_or_string_is_left_alone(self):
        """The sequential-thinking schema declares boolean|string: both are
        legal, so there is no single right coercion and the value stands."""
        out = coerce_tool_args_to_schema({"nextThoughtNeeded": "false"}, SEQ_SCHEMA)
        assert out["nextThoughtNeeded"] == "false"


# ---------------------------------------------------------------------------
# Pure function
# ---------------------------------------------------------------------------

class TestBoolean:
    @pytest.mark.parametrize("s", ["true", "True", "TRUE", " yes ", "1", "on"])
    def test_true_spellings(self, s):
        assert coerce_tool_args_to_schema({"flag": s}, STRICT_SCHEMA)["flag"] is True

    @pytest.mark.parametrize("s", ["false", "False", "no", "0", "off"])
    def test_false_spellings(self, s):
        assert coerce_tool_args_to_schema({"flag": s}, STRICT_SCHEMA)["flag"] is False

    def test_unrecognised_string_is_not_silently_false(self):
        """The manager's coercer maps any non-true string to False; this one
        must leave garbage in place so the tool reports it."""
        assert coerce_tool_args_to_schema({"flag": "maybe"}, STRICT_SCHEMA)["flag"] == "maybe"

    def test_nullable_bool_coerces(self):
        assert coerce_tool_args_to_schema({"nullable_bool": "true"}, STRICT_SCHEMA)["nullable_bool"] is True

    def test_real_bool_untouched(self):
        assert coerce_tool_args_to_schema({"flag": True}, STRICT_SCHEMA)["flag"] is True


class TestNumbers:
    def test_integer_from_string(self):
        assert coerce_tool_args_to_schema({"count": "42"}, STRICT_SCHEMA)["count"] == 42

    def test_integer_from_integral_float_string(self):
        assert coerce_tool_args_to_schema({"count": "3.0"}, STRICT_SCHEMA)["count"] == 3

    def test_integer_from_integral_float(self):
        out = coerce_tool_args_to_schema({"count": 3.0}, STRICT_SCHEMA)
        assert out["count"] == 3 and isinstance(out["count"], int)

    def test_non_integral_left_for_validator(self):
        assert coerce_tool_args_to_schema({"count": "3.5"}, STRICT_SCHEMA)["count"] == "3.5"

    def test_garbage_integer_left(self):
        assert coerce_tool_args_to_schema({"count": "lots"}, STRICT_SCHEMA)["count"] == "lots"

    def test_number_int_and_float(self):
        out = coerce_tool_args_to_schema({"ratio": "2"}, STRICT_SCHEMA)
        assert out["ratio"] == 2 and isinstance(out["ratio"], int)
        out = coerce_tool_args_to_schema({"ratio": "0.25"}, STRICT_SCHEMA)
        assert out["ratio"] == 0.25
        out = coerce_tool_args_to_schema({"ratio": "1e3"}, STRICT_SCHEMA)
        assert out["ratio"] == 1000.0

    def test_optional_integer_anyof_null(self):
        assert coerce_tool_args_to_schema({"maybe": "7"}, STRICT_SCHEMA)["maybe"] == 7

    def test_true_union_not_coerced(self):
        assert coerce_tool_args_to_schema({"either": "7"}, STRICT_SCHEMA)["either"] == "7"

    def test_enum_of_ints_accepts_quoted_value_after_coercion(self):
        """The seam this exists for: validate() checks enum membership on
        the coerced value, so "2" no longer fails an integer enum."""
        raw = {"level": "2"}
        assert validate_tool_args_against_schema("t", raw, STRICT_SCHEMA) is not None
        fixed = coerce_tool_args_to_schema(raw, STRICT_SCHEMA)
        assert fixed["level"] == 2
        assert validate_tool_args_against_schema("t", fixed, STRICT_SCHEMA) is None


class TestStructured:
    def test_json_string_object(self):
        out = coerce_tool_args_to_schema({"opts": '{"a": 1}'}, STRICT_SCHEMA)
        assert out["opts"] == {"a": 1}

    def test_json_string_array(self):
        out = coerce_tool_args_to_schema({"items": '["x", "y"]'}, STRICT_SCHEMA)
        assert out["items"] == ["x", "y"]

    def test_wrong_shape_json_left(self):
        # A JSON array where an object is expected is not "repaired" into
        # something the schema never asked for.
        out = coerce_tool_args_to_schema({"opts": '[1, 2]'}, STRICT_SCHEMA)
        assert out["opts"] == '[1, 2]'

    def test_invalid_json_left(self):
        out = coerce_tool_args_to_schema({"opts": '{not json'}, STRICT_SCHEMA)
        assert out["opts"] == '{not json'


class TestString:
    def test_bare_number_to_string(self):
        assert coerce_tool_args_to_schema({"name": 1234}, STRICT_SCHEMA)["name"] == "1234"

    def test_bool_not_stringified(self):
        # str(True) == "True" is almost never what a string param wanted.
        assert coerce_tool_args_to_schema({"name": True}, STRICT_SCHEMA)["name"] is True


class TestPassthrough:
    def test_unknown_keys_untouched(self):
        args = {"_task_scope": "x", "flag": "true"}
        out = coerce_tool_args_to_schema(args, STRICT_SCHEMA)
        assert out["_task_scope"] == "x" and out["flag"] is True

    def test_does_not_mutate_input(self):
        args = {"flag": "true"}
        coerce_tool_args_to_schema(args, STRICT_SCHEMA)
        assert args == {"flag": "true"}

    @pytest.mark.parametrize("args,schema", [
        ({}, STRICT_SCHEMA), (None, STRICT_SCHEMA),
        ({"flag": "true"}, {}), ({"flag": "true"}, None),
        ({"flag": "true"}, {"properties": "nope"}),
    ])
    def test_degenerate_inputs_return_input(self, args, schema):
        assert coerce_tool_args_to_schema(args, schema) is args or coerce_tool_args_to_schema(args, schema) == args


# ---------------------------------------------------------------------------
# Seam: the executor applies coercion before validation, on the same schema
# ---------------------------------------------------------------------------

class TestExecutorSeam:
    def _source(self):
        # The validation/dispatch block lives in _stream_with_tools_impl
        # (stream_chunks is a module-level wrapper elsewhere).
        return inspect.getsource(StreamingToolExecutor._stream_with_tools_impl)

    def test_coercion_runs_before_validation_with_same_schema(self):
        src = self._source()
        m_c = re.search(r"args = coerce_tool_args_to_schema\(args, (\w+)\)", src)
        m_v = re.search(r"validate_tool_args_against_schema\(\s*tool_name, args, (\w+)\s*\)", src)
        assert m_c, "_stream_with_tools_impl must coerce args via coerce_tool_args_to_schema"
        assert m_v, "_stream_with_tools_impl must validate via validate_tool_args_against_schema"
        assert m_c.start() < m_v.start(), "coercion must precede validation"
        assert m_c.group(1) == m_v.group(1), "coercion and validation must use the same schema"

    def test_coerced_args_are_what_execute(self):
        """The rebinding `args = coerce(...)` must sit in the same scope the
        EXECUTING_TOOL log reads from, so the typed values reach the tool
        (builtin tools have no other repair step)."""
        src = self._source()
        i_c = src.find("args = coerce_tool_args_to_schema(")
        i_e = src.find("EXECUTING_TOOL")
        assert 0 < i_c < i_e
        between = src[i_c:i_e]
        # No reassignment of `args` from a stale source in between.
        assert not re.search(r"\n\s*args = json\.loads", between)
